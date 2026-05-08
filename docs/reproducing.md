# Reproducing the v0.7.0 A2A campaign

Two-droplet topology, full DIY. **Bring your own DigitalOcean account + xAI API
key.** Cost: ~$3 per round (~$0.04/hr × 2 droplets × ~3hr per round including
provision/teardown).

## 0 — prerequisites

* DigitalOcean account with droplet quota of at least 2× `s-4vcpu-16gb-amd`.
* DigitalOcean API token with droplet:write, vpc:write, firewall:write.
* SSH key registered with DigitalOcean. (The team's working key is
  fingerprint `bf:0b:e1:92:6f:ea:0d:1d:e4:96:ee:ac:71:73:ed:4e`, name
  `ai-memory-ai2ai-gate`, key id `55757076`.)
* xAI API key with access to `grok-4.20-0309-reasoning`.
* Local `terraform >= 1.6.0`, `python3 >= 3.11`, `jq`, `ssh`, `scp`.
* A pre-built `ai-memory` binary at `target/release/ai-memory` (commit `dfb184f`
  or later from `alphaonedev/ai-memory-mcp`).

## 1 — clone + env

```bash
git clone https://github.com/alphaonedev/ai-memory-a2a-v0.7.0.git
cd ai-memory-a2a-v0.7.0
cp .env.example .env
$EDITOR .env             # fill in DIGITALOCEAN_TOKEN, XAI_API_KEY, paths
set -a && source .env && set +a
```

## 2 — provision the droplets + VPC

```bash
cd terraform
terraform init
terraform apply \
  -var "do_token=$DIGITALOCEAN_TOKEN" \
  -var "ssh_key_fingerprint=$DIGITALOCEAN_SSH_KEY_FINGERPRINT" \
  -var "campaign_id=v0.7.0-myrun-r1"
terraform output -json > /tmp/tf.json
export OPENCLAW_PUB=$(jq -r '.openclaw_node.value.public' /tmp/tf.json)
export OPENCLAW_PRIV=$(jq -r '.openclaw_node.value.private' /tmp/tf.json)
export HERMES_PUB=$(jq   -r '.hermes_node.value.public'   /tmp/tf.json)
export HERMES_PRIV=$(jq  -r '.hermes_node.value.private'  /tmp/tf.json)
cd ..
```

## 3 — bootstrap both nodes

```bash
./scripts/boot_openclaw.sh "$OPENCLAW_PUB" "$HERMES_PRIV"
./scripts/boot_hermes.sh   "$HERMES_PUB"   "$OPENCLAW_PRIV"
```

This stages `ai-memory` to `/usr/local/bin/`, installs the agent runtime,
enables the `ai-memory` systemd unit with `AI_MEMORY_AUDIT_DIR=/var/log/ai-memory/audit/`,
and verifies the daemon is listening on `:19077`.

## 4 — run a single scenario locally

```bash
export NODE1_IP=$OPENCLAW_PUB     NODE1_PRIV=$OPENCLAW_PRIV
export NODE2_IP=$HERMES_PUB       NODE2_PRIV=$HERMES_PRIV
export NODE3_IP=$NODE1_IP         NODE3_PRIV=$NODE1_PRIV
export AGENT_GROUP=mixed          TLS_MODE=mtls
python3 scenarios/52_a2a_link_signed.py | jq .
```

## 5 — run a full round

```bash
./scripts/collect_reports.sh "v0.7.0-myrun-r1"
```

This iterates the scenario manifest, runs each scenario in turn, dumps per-
scenario JSON into `runs/v0.7.0-myrun-r1/`, and writes
`runs/v0.7.0-myrun-r1/a2a-summary.json`.

To re-render the Pages site against the latest run:

```bash
python3 scripts/render_pages.py
mkdocs serve
```

## 6 — teardown

```bash
cd terraform
terraform destroy -var "do_token=$DIGITALOCEAN_TOKEN" \
                  -var "ssh_key_fingerprint=$DIGITALOCEAN_SSH_KEY_FINGERPRINT" \
                  -var "campaign_id=v0.7.0-myrun-r1"
```

The droplets also have an 8-hour dead-man switch — even a forgotten run
self-destructs.

## 7 — replicate the official cert run

The CI pipeline at `.github/workflows/two-rounds.yml` does steps 2-6 unattended
twice in a row. Dispatch:

```bash
gh workflow run two-rounds.yml --repo alphaonedev/ai-memory-a2a-v0.7.0 \
  --ref main \
  -f ai_memory_git_ref=v0.7.0 \
  -f campaign_prefix=v0.7.0-cert
```

The workflow exits non-zero unless BOTH rounds land at 100% PASS.
