#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 50 — SQLCipher at-rest encryption (v0.6.3).

Validates the encrypted-DB story end to end:

  1. Detect-and-skip if the daemon on the test node was not built with
     the `sqlcipher` cargo feature (or no key is configured) — without
     it the file is a plain sqlite db and nothing under test exists.
  2. Write 100 memories under a controlled namespace.
  3. Stop the daemon, attempt to open the on-disk file with the
     CONFIGURED key — rows must survive (count >= 100).
  4. Re-open with a wrong key — sqlite3 must fail with an explicit
     "PRAGMA key" / "file is not a database" / "encrypted" error and
     return zero rows.
  5. Restart the daemon at the end (best-effort cleanup).

The "wrong key" probe is the load-bearing assertion: a daemon that
silently falls back to plaintext on key mismatch would let cold backups
be read without authentication.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "50"
WRITES = 100
DB_PATH = "/var/lib/ai-memory/a2a.db"


def _read_capabilities(h: Harness, ip: str) -> dict | None:
    _, doc = h.http_on(ip, "GET", "/api/v1/capabilities", include_status=True)
    if isinstance(doc, dict) and doc.get("http_code") == 200:
        body = doc.get("body")
        if isinstance(body, dict):
            return body
    return None


def _has_sqlcipher_feature(cap: dict | None) -> bool:
    """Best-effort detection from the capabilities document."""
    if not isinstance(cap, dict):
        return False
    feats = cap.get("features") or {}
    if isinstance(feats, dict):
        for k in ("sqlcipher", "encryption_at_rest", "encrypted_db"):
            if feats.get(k):
                return True
    if isinstance(feats, list):
        for v in feats:
            if isinstance(v, str) and "sqlcipher" in v.lower():
                return True
    # v0.6.3 may surface this under a `security` block.
    sec = cap.get("security")
    if isinstance(sec, dict) and sec.get("encryption_at_rest"):
        return True
    return False


def _read_pragma_key(h: Harness, ip: str) -> str:
    """Pull the configured PRAGMA key off the host's env file. Returns
    empty string if not present."""
    cmd = (
        "grep -E '^(AI_MEMORY_DB_KEY|SQLCIPHER_KEY|DB_KEY)=' "
        "/etc/ai-memory-a2a/env 2>/dev/null | head -n1 | "
        "sed -E 's/^[^=]+=//; s/^\"//; s/\"$//'"
    )
    r = h.ssh_exec(ip, cmd, timeout=15)
    return (r.stdout or "").strip()


def _open_with_key(h: Harness, ip: str, key: str) -> tuple[bool, str, int]:
    """Attempt to open the DB with `key` and count rows in `memories`.

    Returns (success, stderr_excerpt, row_count).
    """
    if key:
        sql = (
            f"PRAGMA key = '{key}'; "
            "SELECT COUNT(*) FROM memories;"
        )
    else:
        sql = "SELECT COUNT(*) FROM memories;"
    cmd = f"sqlite3 -bail -cmd '.timeout 5000' {DB_PATH} \"{sql}\" 2>&1"
    r = h.ssh_exec(ip, cmd, timeout=20)
    out = (r.stdout or "").strip()
    # sqlite3 prints both rows and errors to stdout when 2>&1 is used.
    rows = 0
    err = ""
    for line in out.splitlines():
        s = line.strip()
        if s.isdigit():
            rows = int(s)
        elif "Error" in s or "encrypted" in s.lower() or "not a database" in s.lower():
            err = s
    success = (r.returncode == 0 and rows > 0 and not err)
    return success, err or out[:200], rows


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)

    cap = _read_capabilities(h, h.node1_ip)
    if not _has_sqlcipher_feature(cap):
        h.skip(
            "sqlcipher feature not advertised in /api/v1/capabilities — "
            "daemon was not built with --features sqlcipher",
            capabilities_features=(cap or {}).get("features"),
        )

    pragma_key = _read_pragma_key(h, h.node1_ip)
    if not pragma_key:
        h.skip(
            "no PRAGMA key found in /etc/ai-memory-a2a/env "
            "(AI_MEMORY_DB_KEY/SQLCIPHER_KEY/DB_KEY) — cannot probe at-rest encryption"
        )
    log(f"  detected PRAGMA key length={len(pragma_key)}")

    ns = f"scenario50-cipher-{new_uuid()[:6]}"
    log(f"writing {WRITES} memories in {ns}")
    written = 0
    for i in range(WRITES):
        rc, _ = h.write_memory(h.node1_ip, "ai:alice", ns,
                               title=f"cipher-{i}", content=new_uuid("cph-"))
        if rc == 0:
            written += 1
    log(f"  wrote {written}/{WRITES}")
    h.settle(5, reason="WAL flush before stop")

    # Force a checkpoint so the WAL contents land in the main db file.
    log("WAL checkpoint via daemon-side sqlite3")
    h.ssh_exec(h.node1_ip, f"sqlite3 {DB_PATH} 'PRAGMA wal_checkpoint(TRUNCATE);'",
               timeout=15)

    log("stopping ai-memory daemon on node-1")
    stop_r = h.ssh_exec(h.node1_ip, "systemctl stop ai-memory-a2a", timeout=30)
    log(f"  stop rc={stop_r.returncode}")

    try:
        log("probe 1: open with CORRECT key — rows must survive")
        ok_right, err_right, rows_right = _open_with_key(h, h.node1_ip, pragma_key)
        log(f"  ok={ok_right} rows={rows_right} err={err_right!r}")

        log("probe 2: open with WRONG key — must fail with explicit error")
        wrong_key = "wrong-key-" + new_uuid()[:8]
        ok_wrong, err_wrong, rows_wrong = _open_with_key(h, h.node1_ip, wrong_key)
        log(f"  ok={ok_wrong} rows={rows_wrong} err={err_wrong!r}")
    finally:
        log("restarting ai-memory daemon on node-1 (cleanup)")
        h.ssh_exec(h.node1_ip, "systemctl start ai-memory-a2a", timeout=30)
        h.settle(8, reason="daemon warmup")

    reasons: list[str] = []
    passed = True
    if not ok_right:
        passed = False
        reasons.append(
            f"correct-key probe failed: rows={rows_right} err={err_right!r}"
        )
    if rows_right < written:
        passed = False
        reasons.append(
            f"correct-key probe saw {rows_right} rows, expected >= {written}"
        )
    if ok_wrong or rows_wrong > 0:
        passed = False
        reasons.append(
            f"wrong-key probe SUCCEEDED ({rows_wrong} rows) — at-rest encryption broken"
        )
    err_lower = err_wrong.lower()
    explicit_error = (
        "pragma key" in err_lower
        or "not a database" in err_lower
        or "encrypted" in err_lower
        or "malformed" in err_lower
    )
    if not explicit_error:
        passed = False
        reasons.append(
            f"wrong-key error not explicit ({err_wrong!r}); want PRAGMA-key-style failure"
        )

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        namespace=ns,
        memories_written=written,
        rows_with_correct_key=rows_right,
        rows_with_wrong_key=rows_wrong,
        correct_key_error=err_right,
        wrong_key_error=err_wrong,
        explicit_pragma_error=explicit_error,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
