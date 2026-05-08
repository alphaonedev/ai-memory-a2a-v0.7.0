# Security

What this campaign assumes and what it proves.

## Trust boundaries

| Boundary | Identity | Mechanism |
|----------|----------|-----------|
| GitHub Actions runner ↔ droplet (control plane) | DO-registered SSH key | `ssh -o StrictHostKeyChecking=no` (first-touch trust on a freshly-minted droplet) |
| openclaw-node ↔ hermes-node (data plane)        | per-campaign mTLS material | rustls-pinned client + server certs |
| droplet ↔ xAI API                               | xAI bearer token | HTTPS to `api.x.ai` (TLS only — xAI does not pin) |
| ai-memory write/read enforcement                | `metadata.agent_id` + `governance.write` policy | F8 `permissions.mode=enforce` (v0.7.0 default) |

## mTLS material

* CA + server cert + client cert are minted ephemerally per campaign on the
  GitHub runner, scp'd to both droplets, and committed nowhere.
* The campaign's terraform output records public + private IPs only — never
  cert bytes.
* Cert paths on droplets: `/etc/ai-memory-a2a/tls/{ca.pem,server.pem,server.key,client.pem,client.key}`.
* Anonymous TLS connections (no client cert) are refused at the rustls layer
  — covered by S21.

## Cloud-firewall rules

* `inbound  22/tcp  ← 0.0.0.0/0`  (ssh; restrict to runner IP if running this
  for production).
* `inbound  19077/tcp ← VPC peer only` (the other droplet's private IP).
* `outbound ALL`                       (xAI API + GitHub release tarballs).
* The DO cloud firewall enforces this; the droplet's local `ufw` is a defense-
  in-depth backup.

## Dead-man switch

Both droplets carry an 8-hour systemd-timer that calls
`shutdown -P +0` if the campaign exceeds the budget. Mirrors
`ai-memory-ship-gate`. The terraform tag `auto-destroy` lets a sweeper find
stragglers.

## Audit chain

* `AI_MEMORY_AUDIT_DIR=/var/log/ai-memory/audit/` is set in the systemd unit
  on BOTH nodes.
* Every store / recall / link / consolidate / find_paths / governance op
  appends a hash-chained line to a daily-rotated log.
* `ai-memory audit verify --dir /var/log/ai-memory/audit/` walks the chain
  and exits 0 only if every `prev_hash` matches the previous line's `hash`.
* S57 enforces:
  * post-workload `verify` rc=0
  * tampered chain `verify` rc≠0 with `chain` / `hash` / `tamper` in the message
  * post-`systemctl restart ai-memory` `verify` rc=0 (continuity)

## Key custody

* Every key generated on the runner is held only in the workflow's runner
  filesystem (`$RUNNER_TEMP`) and on the droplet (`/etc/ai-memory-a2a/tls/`).
* Both locations are destroyed at campaign end (runner: ephemeral; droplet:
  destroyed by terraform).
* No key ever lands in git, in artifacts, or in logs. CI guards: workflow
  uses `::add-mask::` for any cert bytes that touch stdout.

## Secrets we DO want in git

* The DO SSH **public** key fingerprint and key id (in `.env.example`).
  Public material; identifying which key the campaign uses is a feature
  (lets external reviewers verify droplet ownership).

## Scope explicitly NOT covered

* **HSM / KMS-backed signing** for v0.7.0 link signatures. v0.7.0 uses a
  daemon-local Ed25519 keypair generated on first `serve`. KMS support is
  deferred to v0.7.1.
* **TLS pinning** of the xAI endpoint. We rely on the system trust store.
* **Per-namespace at-rest encryption** beyond the default SQLCipher key
  derived at boot. S50 covers SQLCipher round-trip but does not exercise
  per-namespace KEK rotation.
