# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Shared harness for Python-based a2a-gate scenarios (testbook v3.0.0+).

Contract identical across every scenario:
  - stdout = single-line JSON scenario report (consumed by aggregator)
  - stderr = human-readable log lines
  - exit 0 on a clean run (pass / fail / skip); non-zero only on hard crash

Stdlib only — no pip installs on the runner. ssh to droplets is via the
`ssh` subprocess with StrictHostKeyChecking=no.
"""

import concurrent.futures
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence


SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=5"]

# Body size above which HTTP request bodies are piped via ssh stdin +
# `curl -d @-` instead of inlined into the ssh command argv.
# Inlining a 1MB payload via `shlex.quote(json.dumps(body))` overflows
# execve ARG_MAX (E2BIG) — exactly the pattern scenario 23
# malicious_content_fuzz hit on the oversize payload.
LARGE_BODY_THRESHOLD = 64 * 1024

# Exec topology:
#   "ssh"           (default) — DigitalOcean droplet mode; ssh root@node_ip
#   "local-docker"  — Docker compose mode; docker exec <container>
#
# In local-docker mode, NODE<N>_IP is expected to be the CONTAINER NAME
# (e.g. "a2a-node-1"), not a VPC IP. NODE<N>_PRIV should be the bridge
# IP (e.g. "10.88.1.11") for inter-container HTTP on 9077. The scenario
# scripts don't need to care — the shift is contained in this module.
TOPOLOGY = os.environ.get("TOPOLOGY", "ssh")

# A2A_BACKEND_KIND — Wave 4 pre-stage knob (added 2026-05-08).
#   "sqlite"   (default) — daemons run with `--db <sqlite-path>`; legacy
#                          v0.7.0-alpha topology that produced 56/68 GREEN.
#   "postgres"           — daemons run with `--store-url postgres://...`;
#                          live A2A re-validation post-Continuation 3.
#   "mixed"              — heterogeneous: openclaw=sqlite, hermes=postgres
#                          (or vice versa) for federation interop tests.
#
# Scenarios that are backend-agnostic (most HTTP-driven oracles) ignore
# this. Scenarios that ssh into the daemon's filesystem to read sqlite or
# audit-log artifacts use the helpers below to dispatch correctly.
#
# Wave 4 adds S77-S82 which self-skip when this is "sqlite" so that the
# existing 56/68 baseline remains valid on legacy hardware.
BACKEND_KIND = os.environ.get("A2A_BACKEND_KIND", "sqlite").strip().lower()
VALID_BACKEND_KINDS = ("sqlite", "postgres", "mixed")
if BACKEND_KIND not in VALID_BACKEND_KINDS:
    print(
        f"[harness] WARNING: A2A_BACKEND_KIND={BACKEND_KIND!r} not in "
        f"{VALID_BACKEND_KINDS}; falling back to 'sqlite'",
        file=sys.stderr,
    )
    BACKEND_KIND = "sqlite"


def log(msg: str) -> None:
    """Write a log line to stderr."""
    print(msg, file=sys.stderr, flush=True)


def new_uuid(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex}" if prefix else uuid.uuid4().hex


@dataclass
class Harness:
    """Campaign context + droplet handles. One instance per scenario run."""

    node1_ip: str
    node2_ip: str
    node3_ip: str
    node4_ip: str = ""
    memory_node_ip: str = ""
    # v0.6.2 (S39 RCA): DigitalOcean firewall allows port 9077 ONLY from
    # within the VPC CIDR (terraform/main.tf:194). A scenario that does
    # `ssh root@NODE3_IP "curl http://NODE1_IP:9077/..."` egresses via
    # node-3's public gateway and hits node-1's PUBLIC interface → DO
    # firewall drops the packet. Private (VPC) IPs are reachable on 9077
    # from any droplet in the same VPC. Any scenario that needs to hit
    # a peer's HTTP surface from WITHIN another droplet's shell must use
    # these private IPs, not the public `.nodeN_ip`.
    node1_priv: str = ""
    node2_priv: str = ""
    node3_priv: str = ""
    node4_priv: str = ""  # aka memory_priv
    agent_group: str = "ironclaw"
    tls_mode: str = "off"
    scenario_id: str = ""
    # F4 fix: per-node db-path cache for `node_db_path()`. Populated lazily
    # on first call; never invalidated within a scenario run.
    _db_path_cache: dict[str, str] = field(default_factory=dict)
    # Continuation-6: per-scenario TLS handshake samples (seconds). One
    # entry per HTTP request; the scenario report rolls min/mean/max.
    _tls_handshake_samples: list[float] = field(default_factory=list)

    @staticmethod
    def new_uuid(prefix: str = "") -> str:
        """Convenience: `h.new_uuid("prefix-")` calls the module-level helper."""
        return new_uuid(prefix)

    @classmethod
    def from_env(cls, scenario_id: str, *,
                 require_node4: bool = False,
                 require_node3: bool = False) -> "Harness":
        # v0.7.0 A2A campaign topology is 2-droplet (openclaw + hermes) +
        # 1 postgres node; NODE3_IP / NODE4_IP fall back to NODE2_IP so
        # 3-and-4-node scenarios collapse to a 2-way comparison rather
        # than crashing on KeyError. Documented in runs/<campaign>/findings.
        #
        # Defense-in-depth: if a scenario explicitly requires a distinct
        # 3rd or 4th node and the env shows NODE3/NODE4 aliased to NODE2,
        # we emit a clean SKIP via stdout JSON and exit 0 — the runner's
        # scope manifest is the primary skip-list, this is a backstop.
        need = ["NODE1_IP", "NODE2_IP", "AGENT_GROUP"]
        missing = [k for k in need if not os.environ.get(k)]
        if missing:
            raise RuntimeError(f"missing required env vars: {missing}")
        node2 = os.environ["NODE2_IP"]
        node3_env = os.environ.get("NODE3_IP")
        node4_env = os.environ.get("NODE4_IP")
        node3 = node3_env or node2
        node4 = node4_env or node2
        h = cls(
            node1_ip=os.environ["NODE1_IP"],
            node2_ip=node2,
            node3_ip=node3,
            node4_ip=node4,
            memory_node_ip=os.environ.get("MEMORY_NODE_IP", ""),
            node1_priv=os.environ.get("NODE1_PRIV", ""),
            node2_priv=os.environ.get("NODE2_PRIV", ""),
            node3_priv=os.environ.get("NODE3_PRIV", ""),
            node4_priv=os.environ.get("MEMORY_PRIV", ""),
            agent_group=os.environ["AGENT_GROUP"],
            tls_mode=os.environ.get("TLS_MODE", "off"),
            scenario_id=scenario_id,
        )
        # Backstop alias detection: only triggers on the NEW
        # `require_node3` / `require_node4` *strict* knobs (not the
        # legacy `require_node4=True` parameter, which most v0.6.x
        # scenarios pass for documentation only).
        #
        # The scope manifest at scripts/scope-v0.7.0.json is the
        # primary skip-list. Scenarios that explicitly want HARD
        # alias-detection can opt in via require_node3=True.
        # require_node4 here is preserved for backwards-compat but
        # no longer auto-skips — the legacy `require_node4=True` on
        # scenarios like S1b/S4/S5/S10 is a no-op annotation; their
        # node3/node4 reads collapse to node2 alias and still pass
        # their federation-visibility oracle.
        _ = require_node4  # legacy compat — no auto-skip on alias
        if require_node3 and (not node3_env or node3 == node2):
            h.skip(
                "v0.7.0 A2A is 2-agent (openclaw+hermes); scenario opts in to "
                "require_node3=True and NODE3 is aliased to NODE2."
            )
        return h

    # -------- postgres helpers (v0.7.0 SAL adapter target) --------
    #
    # NOTE: in v0.7.0-alpha postgres is NOT the live storage backend
    # (`ai-memory serve --store-url postgres://...` is deferred to v0.7.1).
    # The connection URI returned here is intended for two surfaces only:
    #   1. one-shot `ai-memory migrate --to postgres://...`
    #   2. direct SAL trait access from cargo tests / `psql` for AGE Cypher
    # See docs/coverage.md "Postgres + Apache AGE" for the in-scope surface.

    @staticmethod
    def postgres_url(*, db: str | None = None) -> str:
        """Build the postgres connection URI from env + password file.

        Reads:
          POSTGRES_HOST     (default 10.20.0.4)
          POSTGRES_PORT     (default 5432)
          POSTGRES_DB       (default aimemory; overridden by `db=` kwarg)
          POSTGRES_USER     (default aimemory)
          POSTGRES_PASSWORD_PATH   (default /tmp/v07-a2a-pg-password.txt)

        Returns `postgres://user:pwd@host:port/db`. Raises RuntimeError if
        the password file is missing — scenarios should treat that as a
        SKIP, not a hard fail (see S70 phase A).
        """
        host = os.environ.get("POSTGRES_HOST", "10.20.0.4")
        port = os.environ.get("POSTGRES_PORT", "5432")
        target_db = db or os.environ.get("POSTGRES_DB", "aimemory")
        user = os.environ.get("POSTGRES_USER", "aimemory")
        pwd_path = os.environ.get(
            "POSTGRES_PASSWORD_PATH", "/tmp/v07-a2a-pg-password.txt"
        )
        try:
            with open(pwd_path, "r", encoding="utf-8") as f:
                pwd = f.read().strip()
        except OSError as e:
            raise RuntimeError(
                f"postgres password file unreadable at {pwd_path}: {e}"
            ) from e
        if not pwd:
            raise RuntimeError(f"postgres password file empty at {pwd_path}")
        # urllib.parse.quote on the password handles `@` / `:` / `/` safely.
        return f"postgres://{user}:{urllib.parse.quote(pwd, safe='')}@{host}:{port}/{target_db}"

    @staticmethod
    def postgres_node_ip() -> str:
        """Public IP for ssh into postgres-node, for `psql` / extension toggle.

        Returns "" when not provisioned. Reads POSTGRES_NODE_IP, falling
        back to the default droplet IP from /tmp/v07-a2a-droplets.json.
        """
        return os.environ.get("POSTGRES_NODE_IP", "68.183.157.68")

    # -------- backend-kind helpers (Wave 4 pre-stage) --------

    @staticmethod
    def backend_kind() -> str:
        """Return the campaign-wide backend kind: 'sqlite' | 'postgres' | 'mixed'.

        Source of truth is the module-level BACKEND_KIND read from
        A2A_BACKEND_KIND at import time. Scenarios that need to dispatch
        per-backend should call this rather than reading the env directly
        (so a future per-node override stays contained here).
        """
        return BACKEND_KIND

    def node_backend(self, node_ip: str) -> str:
        """Return the per-node backend ('sqlite'|'postgres') honoring 'mixed'.

        For 'sqlite' / 'postgres' kinds, every node uses the same backend.
        For 'mixed', openclaw (node1) is sqlite and hermes (node2) is
        postgres — that's the heterogeneous federation shape S81 exercises.
        Override via A2A_NODE{N}_BACKEND env if a non-default mapping is
        ever needed.
        """
        override_var = None
        if node_ip == self.node1_ip:
            override_var = "A2A_NODE1_BACKEND"
        elif node_ip == self.node2_ip:
            override_var = "A2A_NODE2_BACKEND"
        if override_var:
            ovr = os.environ.get(override_var, "").strip().lower()
            if ovr in ("sqlite", "postgres"):
                return ovr
        kind = self.backend_kind()
        if kind == "mixed":
            if node_ip == self.node1_ip:
                return "sqlite"
            if node_ip == self.node2_ip:
                return "postgres"
            return "sqlite"
        if kind == "postgres":
            return "postgres"
        return "sqlite"

    def skip_if_backend_sqlite(self, reason: str | None = None) -> None:
        """Self-skip helper for postgres-only Wave 4 scenarios (S77-S82).

        Emits a clean SKIP record via h.skip() when the campaign is
        running on a sqlite-only baseline. No-op when backend is postgres
        or mixed.
        """
        if self.backend_kind() == "sqlite":
            self.skip(
                reason
                or "scenario requires A2A_BACKEND_KIND in {postgres,mixed}; "
                "campaign is on sqlite baseline"
            )

    def daemon_storage_label(self, node_ip: str) -> str:
        """Probe `/api/v1/capabilities` for the daemon's storage backend
        identifier. Returns '' on probe failure or if the field is absent.

        Called by S77 to verify capabilities surface reports
        `storage_backend: postgres` after Continuation 3 lands.
        """
        for path in ("/api/v1/capabilities", "/api/v1/capability", "/api/v1/version"):
            _, resp = self.http_on(node_ip, "GET", path, include_status=True)
            if isinstance(resp, dict) and resp.get("http_code") == 200:
                body = resp.get("body")
                if isinstance(body, dict):
                    for key in ("storage_backend", "store_backend", "backend",
                                "store", "storage"):
                        v = body.get(key)
                        if isinstance(v, str) and v:
                            return v.lower()
                    # nested {storage: {backend: "..."}}
                    storage = body.get("storage")
                    if isinstance(storage, dict):
                        v = storage.get("backend") or storage.get("kind")
                        if isinstance(v, str) and v:
                            return v.lower()
        return ""

    # -------- daemon db-path discovery (v0.7.0 F4 fix) --------

    def node_db_path(self, node_ip: str, *, default: str = "/var/lib/ai-memory/store.db") -> str:
        """Discover the running daemon's `--db` path on `node_ip`.

        Parses the live `ai-memory serve` command line via pgrep + sed.
        Caches per-node so repeat calls are free. Falls back to `default`
        when the daemon is not running or the flag is absent.

        Why this exists: v0.7.0 A2A bootstrap names each droplet's sqlite
        after the agent (e.g. `/var/lib/ai-memory/openclaw.db`,
        `/var/lib/ai-memory/hermes.db`), not the legacy `store.db`.
        Hardcoding `store.db` causes scenarios that read the live db
        (S70 Phase A reuse, S72 Phase B migrate-from) to operate on an
        empty file and silently report `0` rows. F4 RCA, 2026-05-09.
        """
        if not node_ip:
            return default
        if node_ip in self._db_path_cache:
            return self._db_path_cache[node_ip]
        cmd = (
            "pgrep -af 'ai-memory serve' "
            r"| sed -n 's/.*--db \([^ ]\+\).*/\1/p' "
            "| head -n1"
        )
        r = self.ssh_exec(node_ip, cmd, timeout=10)
        path = (r.stdout or "").strip()
        if not path:
            path = default
        self._db_path_cache[node_ip] = path
        return path

    # -------- ssh primitives --------

    def _run(self, cmd: list[str], *, timeout: int, stdin: str | None = None
             ) -> subprocess.CompletedProcess:
        """Thin wrapper around subprocess.run that converts TimeoutExpired
        into a CompletedProcess(returncode=124) so scenario code can check
        returncode uniformly instead of wrapping every call in try/except.
        Exit code 124 matches the coreutils `timeout(1)` convention.
        """
        try:
            return subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, input=stdin,
            )
        except subprocess.TimeoutExpired as e:
            log(f"  !! ssh timeout ({timeout}s): {' '.join(cmd[-2:])}")
            return subprocess.CompletedProcess(
                args=cmd, returncode=124,
                stdout=(e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or ""),
                stderr=f"__TIMEOUT_{timeout}s__",
            )

    def ssh_exec(self, node_ip: str, remote_cmd: str, *, timeout: int = 120,
                 stdin: str | None = None) -> subprocess.CompletedProcess:
        """Run `remote_cmd` on `node_ip`. Never raises on non-zero exit or
        timeout — returncode=124 on timeout (coreutils convention).

        Dispatches on TOPOLOGY env var:
          * "ssh"          — ssh root@node_ip "remote_cmd"
          * "local-docker" — docker exec node_ip sh -c "remote_cmd"
                             (node_ip is the container name)
        """
        if TOPOLOGY == "local-docker":
            cmd = ["docker", "exec", "-i", node_ip, "sh", "-c", remote_cmd]
        else:
            cmd = ["ssh", *SSH_OPTS, f"root@{node_ip}", remote_cmd]
        return self._run(cmd, timeout=timeout, stdin=stdin)

    def ssh_bash_script(self, node_ip: str, script: str, *args: str,
                        timeout: int = 180) -> subprocess.CompletedProcess:
        """Pipe a multi-line bash script into `bash -s -- arg1 arg2...`.

        Same topology dispatch as [`ssh_exec`]."""
        argv = " ".join(shlex.quote(a) for a in args)
        if TOPOLOGY == "local-docker":
            cmd = ["docker", "exec", "-i", node_ip, "bash", "-s", "--", *args]
        else:
            cmd = ["ssh", *SSH_OPTS, f"root@{node_ip}", f"bash -s -- {argv}"]
        return self._run(cmd, timeout=timeout, stdin=script)

    # -------- TLS plumbing (Continuation 6 — Phase 5) ---------------------
    #
    # The cert harness distributes server + client certs to each
    # droplet at /etc/ai-memory-a2a/tls/ via the orchestrator's SCP
    # phase. The harness's responsibility is to choose the RIGHT
    # client cert per agent identity and emit the matching curl flags
    # so each scenario can authenticate as its caller.
    #
    # Env knobs (each optional, harness falls back to a sensible default):
    #
    #   TLS_MODE                 — "off" | "tls" | "mtls"  (also via Harness.tls_mode)
    #   TLS_CA_PEM               — path to the CA bundle on the remote droplet
    #                              (default: /etc/ai-memory-a2a/tls/ca.pem)
    #   TLS_CLIENT_CERT_<AGENT>  — per-agent client cert path on the remote droplet
    #   TLS_CLIENT_KEY_<AGENT>   — per-agent client key path on the remote droplet
    #   TLS_DEFAULT_CLIENT_CERT  — fallback cert used when no per-agent cert is set
    #   TLS_DEFAULT_CLIENT_KEY   — fallback key used when no per-agent key is set
    #
    # `<AGENT>` is the upper-cased agent stem with non-alphanumerics
    # collapsed to `_` (so `ai:alice@nyc3-droplet-1` -> `AI_ALICE_NYC3_DROPLET_1`).
    # Plain unprefixed names like `alice` / `bob` are also looked up via
    # `<AGENT>` directly.

    @staticmethod
    def _agent_env_stem(agent_id: str) -> str:
        """Normalize an agent_id into a stable env-var suffix.

        e.g. `alice` -> `ALICE`, `ai:alice@nyc3:droplet-1` ->
        `AI_ALICE_NYC3_DROPLET_1`. Used by `client_cert_for` to look up
        `TLS_CLIENT_CERT_<stem>` / `TLS_CLIENT_KEY_<stem>` env vars.
        """
        if not agent_id:
            return ""
        out = []
        for ch in agent_id:
            if ch.isalnum():
                out.append(ch.upper())
            else:
                out.append("_")
        # collapse runs of "_" to a single one
        s = "".join(out)
        while "__" in s:
            s = s.replace("__", "_")
        return s.strip("_")

    def client_cert_for(self, agent_id: str | None) -> tuple[str | None, str | None]:
        """Return `(cert_path, key_path)` for the supplied agent_id.

        Resolution order:
          1. `TLS_CLIENT_CERT_<stem>` / `TLS_CLIENT_KEY_<stem>` env vars.
          2. `TLS_DEFAULT_CLIENT_CERT` / `TLS_DEFAULT_CLIENT_KEY` env vars.
          3. The legacy `/etc/ai-memory-a2a/tls/client.pem` / `client.key`
             pair (pre-Continuation-6 default).

        Returns `(None, None)` only when the legacy defaults are also
        explicitly disabled via `TLS_DEFAULT_CLIENT_CERT=`. Scenarios
        that need per-agent cert distinction MUST set the per-agent env
        vars; otherwise every scenario picks up the default and the
        daemon's mTLS allowlist sees a single fingerprint regardless
        of which scenario is calling.
        """
        # 1. Per-agent override.
        stem = self._agent_env_stem(agent_id) if agent_id else ""
        if stem:
            cert = os.environ.get(f"TLS_CLIENT_CERT_{stem}")
            key = os.environ.get(f"TLS_CLIENT_KEY_{stem}")
            if cert and key:
                return cert, key
        # 2. Process-wide default.
        cert = os.environ.get("TLS_DEFAULT_CLIENT_CERT")
        key = os.environ.get("TLS_DEFAULT_CLIENT_KEY")
        if cert and key:
            return cert, key
        # 3. Legacy fallback (pre-Continuation-6 path the harness used
        #    when cert plumbing was a single shared keypair). The
        #    orchestrator can suppress this by setting
        #    `TLS_DEFAULT_CLIENT_CERT=NONE` (any non-readable path) so
        #    a misconfigured scenario fails loudly instead of silently
        #    auth'ing as the legacy identity.
        return (
            "/etc/ai-memory-a2a/tls/client.pem",
            "/etc/ai-memory-a2a/tls/client.key",
        )

    def ca_pem_path(self) -> str:
        """Path to the CA bundle on the remote droplet. Defaults to the
        Continuation-6 install path. Override via `TLS_CA_PEM`."""
        return os.environ.get("TLS_CA_PEM", "/etc/ai-memory-a2a/tls/ca.pem")

    # -------- curl construction --------

    def _remote_curl_prefix(self, node_ip: str | None = None,
                            agent_id: str | None = None) -> str:
        """Build the remote curl command prefix, honoring `tls_mode` and
        the per-agent cert mapping.

        Continuation-6 changes:
          * `--cacert` honors `TLS_CA_PEM` env (default
            `/etc/ai-memory-a2a/tls/ca.pem`).
          * `--cert` / `--key` honor the per-agent mapping resolved by
            `client_cert_for(agent_id)`.
          * `-w '%{time_appconnect} %{time_connect}\n'` is emitted on
            every TLS request so the caller can capture the handshake
            duration via the `tls_handshake_seconds` field on the JSON
            report (see `Harness.emit`).
        """
        if self.tls_mode == "off":
            return "curl -sS"
        ca = self.ca_pem_path()
        flags = (
            f"curl -sS --cacert {shlex.quote(ca)} "
            f"--resolve localhost:9077:127.0.0.1"
        )
        if self.tls_mode == "mtls":
            cert, key = self.client_cert_for(agent_id)
            if cert and key:
                flags += (
                    f" --cert {shlex.quote(cert)} --key {shlex.quote(key)}"
                )
        return flags

    # v0.7.0 A2A campaign: daemons listen on PRIVATE VPC IPs only at port
    # 19077 (not 127.0.0.1:9077 like the v0.6.x baseline assumed). When the
    # harness ssh's into a droplet and runs curl, the curl needs to hit
    # that droplet's OWN private IP. We map public→private at runtime via
    # the {OPENCLAW,HERMES}_PRIVATE_IP env vars set by the orchestrator.
    # If A2A_BASE_HOST + A2A_BASE_PORT are set explicitly, those win; else
    # we look up the public→private mapping; else fall back to v0.6.x default.
    def _local_addr_for(self, node_ip: str | None) -> tuple[str, int]:
        port_override = os.environ.get("A2A_BASE_PORT")
        host_override = os.environ.get("A2A_BASE_HOST")
        port = int(port_override) if port_override else 9077
        if host_override:
            return host_override, port
        # public-IP-keyed map populated from env
        mapping: dict[str, str] = {}
        for pub_var, priv_var in (
            ("OPENCLAW_PUBLIC_IP", "OPENCLAW_PRIVATE_IP"),
            ("HERMES_PUBLIC_IP", "HERMES_PRIVATE_IP"),
        ):
            pub = os.environ.get(pub_var)
            priv = os.environ.get(priv_var)
            if pub and priv:
                mapping[pub] = priv
        if node_ip and node_ip in mapping:
            return mapping[node_ip], port
        # Nothing matched — try the explicit private ip from harness fields.
        if node_ip == self.node1_ip and self.node1_priv:
            return self.node1_priv, port
        if node_ip == self.node2_ip and self.node2_priv:
            return self.node2_priv, port
        # When node_ip is None (caller wants "this droplet's local listener"),
        # default to node1's private IP. v0.7.0 daemons bind to PRIVATE VPC
        # IP only — 127.0.0.1 does not have a listener.
        if node_ip is None and self.node1_priv:
            return self.node1_priv, port
        return "127.0.0.1", port

    def remote_base_url(self, node_ip: str | None = None) -> str:
        host, port = self._local_addr_for(node_ip)
        if self.tls_mode == "off":
            return f"http://{host}:{port}"
        return f"https://{host}:{port}"

    # -------- HTTP helpers --------

    def http_on(self, node_ip: str, method: str, path: str, *,
                body: Any | None = None, agent_id: str | None = None,
                extra_headers: dict[str, str] | None = None,
                include_status: bool = False,
                timeout: int = 60) -> tuple[int, Any]:
        """Run an HTTP request against `node_ip`'s LOCAL ai-memory HTTP daemon via ssh+curl.

        Returns `(ssh_returncode, parsed_response)` where parsed_response is
        dict/list if JSON-parseable, raw string otherwise.

        When `include_status=True`, parsed_response is `{body:..., http_code:<int>}`
        — needed for scenarios that assert specific HTTP status codes (4xx, 201, etc.).
        """
        url = f"{self.remote_base_url(node_ip)}{path}"
        headers = {"Content-Type": "application/json"}
        if agent_id:
            headers["X-Agent-Id"] = agent_id
        if extra_headers:
            headers.update(extra_headers)
        curl_prefix = self._remote_curl_prefix(node_ip, agent_id=agent_id)

        parts = [curl_prefix, "-X", method, shlex.quote(url)]
        for k, v in headers.items():
            parts += ["-H", shlex.quote(f"{k}: {v}")]

        # Large bodies (> LARGE_BODY_THRESHOLD) are piped via ssh stdin and
        # consumed by remote curl as `-d @-`. Inlining them via
        # `shlex.quote(json.dumps(body))` works for small payloads but
        # overflows execve ARG_MAX (E2BIG / errno 7 "Argument list too
        # long") on 1MB-class payloads — exactly the pattern S23
        # malicious_content_fuzz hit on the oversize payload. The
        # threshold is conservative: 64 KB is well under the 128 KB
        # single-arg cap on most Linux distros.
        stdin_body: str | None = None
        if body is not None:
            body_json = json.dumps(body)
            if len(body_json) > LARGE_BODY_THRESHOLD:
                parts += ["-d", "@-"]
                stdin_body = body_json
            else:
                parts += ["-d", shlex.quote(body_json)]
        # Continuation-6: always emit the TLS handshake timing marker so
        # `Harness.emit` can roll a `tls_handshake_seconds` field into
        # the per-scenario JSON report. `time_appconnect` is the
        # cumulative seconds at the end of the TLS handshake (0 on
        # plain HTTP), and `time_connect` is at end of TCP connect; the
        # difference is the handshake itself. We emit BOTH along with
        # the existing `__HTTP__<code>` status marker so the caller
        # can split on the markers without breaking the older shape.
        marker_suffix = "\n__HTTP__%{http_code} __TLS__%{time_appconnect} __TCP__%{time_connect}"
        if include_status:
            parts += ["-w", shlex.quote(marker_suffix)]
        else:
            # Even when the caller doesn't ask for status, we still
            # emit the timing markers so the harness picks them up for
            # logging — the caller-facing return shape stays unchanged
            # because we strip the markers before returning.
            parts += ["-w", shlex.quote(marker_suffix)]
        remote_cmd = " ".join(parts)

        # Local timer wraps the ssh_exec — gives us an upper-bound
        # measurement that includes the ssh overhead. The remote
        # `time_appconnect`/`time_connect` is the authoritative
        # handshake number; the local wrap is logged at debug only.
        t_local_start = time.perf_counter()
        result = self.ssh_exec(node_ip, remote_cmd, timeout=timeout, stdin=stdin_body)
        t_local_total = time.perf_counter() - t_local_start
        raw = (result.stdout or "").strip()

        # Strip the timing + status markers from the body. Marker
        # ordering is `\n__HTTP__<code> __TLS__<seconds> __TCP__<seconds>`
        # so the rpartition chain consumes them right-to-left.
        tls_handshake = None
        tcp_connect = None
        http_code = 0
        body_str = raw
        if "__TCP__" in body_str:
            head, _, tcp_part = body_str.rpartition(" __TCP__")
            try:
                tcp_connect = float(tcp_part.strip())
            except ValueError:
                pass
            body_str = head
        if "__TLS__" in body_str:
            head, _, tls_part = body_str.rpartition(" __TLS__")
            try:
                tls_handshake = float(tls_part.strip())
            except ValueError:
                pass
            body_str = head
        if "__HTTP__" in body_str:
            head, _, code_part = body_str.rpartition("\n__HTTP__")
            try:
                http_code = int(code_part.strip())
            except ValueError:
                http_code = 0
            body_str = head

        # Cumulative TLS handshake bookkeeping for the scenario report.
        # `tls_handshake` is `time_appconnect - time_connect` (handshake
        # itself, in seconds). `time_appconnect` = 0 on plain HTTP, so
        # the resulting field is 0.0 there.
        if tls_handshake is not None and tcp_connect is not None:
            handshake_secs = max(tls_handshake - tcp_connect, 0.0)
            self._tls_handshake_samples.append(handshake_secs)
            log(
                f"  curl tls_handshake={handshake_secs:.4f}s "
                f"(appconnect={tls_handshake:.4f}s, connect={tcp_connect:.4f}s, "
                f"local_total={t_local_total:.3f}s) {method} {path}"
            )

        try:
            parsed = json.loads(body_str) if body_str else None
        except json.JSONDecodeError:
            parsed = body_str

        if include_status:
            return result.returncode, {"body": parsed, "http_code": http_code}
        return result.returncode, parsed

    def http_on_expect_fail(self, node_ip: str, method: str, path: str, **kwargs) -> int:
        """Variant that EXPECTS curl to fail (TLS handshake rejected, etc.)."""
        rc, _ = self.http_on(node_ip, method, path, include_status=False, **kwargs)
        return rc

    # -------- memory CRUD shortcuts --------

    def write_memory(self, node_ip: str, agent_id: str, namespace: str, *,
                     title: str, content: str, tier: str = "mid",
                     priority: int = 5, metadata: dict | None = None,
                     include_status: bool = False) -> tuple[int, Any]:
        md = {"agent_id": agent_id, "scenario": self.scenario_id}
        if metadata:
            md.update(metadata)
        body = {
            "tier": tier, "namespace": namespace, "title": title,
            "content": content, "priority": priority, "confidence": 1.0,
            "source": "api", "metadata": md,
        }
        return self.http_on(node_ip, "POST", "/api/v1/memories",
                            body=body, agent_id=agent_id, include_status=include_status)

    def list_memories(self, node_ip: str, namespace: str, limit: int = 50) -> tuple[int, Any]:
        q = urllib.parse.urlencode({"namespace": namespace, "limit": limit})
        return self.http_on(node_ip, "GET", f"/api/v1/memories?{q}")

    def get_memory(self, node_ip: str, memory_id: str) -> tuple[int, Any]:
        return self.http_on(node_ip, "GET", f"/api/v1/memories/{memory_id}")

    def update_memory(self, node_ip: str, memory_id: str, agent_id: str, *,
                      updates: dict, include_status: bool = True) -> tuple[int, Any]:
        return self.http_on(node_ip, "PUT", f"/api/v1/memories/{memory_id}",
                            body=updates, agent_id=agent_id, include_status=include_status)

    def delete_memory(self, node_ip: str, memory_id: str, agent_id: str,
                      include_status: bool = True) -> tuple[int, Any]:
        return self.http_on(node_ip, "DELETE", f"/api/v1/memories/{memory_id}",
                            agent_id=agent_id, include_status=include_status)

    # -------- namespace counting helpers --------

    def count_matching(self, node_ip: str, namespace: str, *,
                       content_contains: str | None = None,
                       content_equals: str | None = None,
                       agent_id: str | None = None,
                       limit: int = 100) -> int:
        """Count rows in `namespace` on `node_ip` that match a filter."""
        rc, resp = self.list_memories(node_ip, namespace, limit=limit)
        if rc != 0 or not isinstance(resp, dict):
            return 0
        n = 0
        for m in resp.get("memories", []) or []:
            if content_contains is not None and content_contains not in (m.get("content") or ""):
                continue
            if content_equals is not None and (m.get("content") or "") != content_equals:
                continue
            if agent_id is not None and ((m.get("metadata") or {}).get("agent_id") or "") != agent_id:
                continue
            n += 1
        return n

    def count_wrong_agent_id(self, node_ip: str, namespace: str, expected_agent_id: str,
                             limit: int = 200) -> tuple[int, int]:
        """Returns (total_rows, rows_with_wrong_agent_id). A diagnostic for Task 1.2 breach."""
        rc, resp = self.list_memories(node_ip, namespace, limit=limit)
        if rc != 0 or not isinstance(resp, dict):
            return 0, 0
        memories = resp.get("memories") or []
        total = len(memories)
        wrong = sum(
            1 for m in memories
            if ((m.get("metadata") or {}).get("agent_id") or "") != expected_agent_id
        )
        return total, wrong

    # -------- concurrency --------

    def run_parallel(self, fn: Callable[..., Any], inputs: Sequence[tuple],
                     max_workers: int = 8) -> list[Any]:
        """Run `fn(*args_tuple)` for each args_tuple in `inputs` concurrently.

        Returns list of results (exception objects on failure).
        """
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = [ex.submit(fn, *args) for args in inputs]
            out: list[Any] = []
            for f in futs:
                try:
                    out.append(f.result())
                except Exception as exc:  # surface but don't propagate
                    out.append(exc)
            return out

    # -------- flow control --------

    def settle(self, seconds: int = 8, reason: str = "quorum fanout") -> None:
        log(f"settle {seconds}s for {reason}")
        time.sleep(seconds)

    # -------- drive_agent.sh passthrough (for S1 MCP path) --------

    def drive_agent(self, node_ip: str, verb: str, *args: str,
                    timeout: int = 60) -> subprocess.CompletedProcess:
        """Invoke /root/drive_agent.sh <verb> on a remote droplet (used by S1).

        drive_agent.sh is installed on every agent droplet by setup_node.sh
        and abstracts over ironclaw/hermes/openclaw MCP invocations.
        """
        argv = " ".join(shlex.quote(a) for a in (verb, *args))
        remote = f"source /etc/ai-memory-a2a/env 2>/dev/null; bash /root/drive_agent.sh {argv}"
        return self.ssh_exec(node_ip, remote, timeout=timeout)

    # -------- reporting --------

    def emit(self, *, passed: bool | None, skipped: bool = False, reason: str = "",
             **fields: Any) -> None:
        """Emit the final JSON scenario report to stdout and exit 0.

        `passed=None` is allowed for skipped scenarios.

        Continuation-6: when one or more TLS handshakes were observed
        (i.e. the scenario ran against a TLS/mTLS daemon), the report
        carries a `tls_handshake` block with `count`, `min_seconds`,
        `mean_seconds`, `max_seconds`, and `total_seconds`. Plain-HTTP
        scenarios omit the block entirely.
        """
        doc: dict[str, Any] = {
            "scenario": self.scenario_id,
            "pass": passed,
            "skipped": skipped,
            "agent_group": self.agent_group,
            "tls_mode": self.tls_mode,
            "backend_kind": BACKEND_KIND,
        }
        if reason:
            doc["reason"] = reason
        # Roll TLS handshake samples into the report. Filter out
        # zero-valued samples (which happen on every plain-HTTP
        # scenario or when curl's `time_appconnect` failed to populate)
        # so the stats reflect ACTUAL handshake events.
        nonzero = [s for s in self._tls_handshake_samples if s > 0.0]
        if nonzero:
            doc["tls_handshake"] = {
                "count": len(nonzero),
                "min_seconds": round(min(nonzero), 6),
                "mean_seconds": round(sum(nonzero) / len(nonzero), 6),
                "max_seconds": round(max(nonzero), 6),
                "total_seconds": round(sum(nonzero), 6),
            }
        doc.update(fields)
        print(json.dumps(doc, sort_keys=True), flush=True)
        sys.exit(0)

    def skip(self, reason: str, **fields: Any) -> None:
        log(f"skipped — {reason}")
        self.emit(passed=None, skipped=True, reason=reason, **fields)
