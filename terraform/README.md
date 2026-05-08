# terraform/

Two-droplet DigitalOcean fixture for the v0.7.0 A2A campaign.

## What this provisions

* A campaign-scoped VPC at `10.250.0.0/20` in `nyc3`.
* Two `s-4vcpu-16gb-amd` droplets:
  * `openclaw-node` (tag `role-openclaw`)
  * `hermes-node` (tag `role-hermes`)
* A cloud firewall:
  * inbound `22/tcp` open (configurable via `ssh_source_cidrs`)
  * inbound `19077/tcp` open ONLY between the two droplets (VPC peer source)
  * outbound any → any
* A defense-in-depth 8-hour dead-man switch wired in via cloud-init
  (`dead-man.timer` → `shutdown -P +0`).

The orchestrator handles the actual install of ai-memory + the agent
frameworks via `scripts/boot_openclaw.sh` and `scripts/boot_hermes.sh`.

## Apply

```bash
terraform init
terraform apply \
  -var "do_token=$DIGITALOCEAN_TOKEN" \
  -var "ssh_key_fingerprint=$DIGITALOCEAN_SSH_KEY_FINGERPRINT" \
  -var "campaign_id=v0.7.0-r1"
```

Outputs:

```bash
terraform output -json
# {
#   "openclaw_node": { "value": { "public": "X.X.X.X", "private": "10.250.0.4" } },
#   "hermes_node":   { "value": { "public": "Y.Y.Y.Y", "private": "10.250.0.5" } },
#   "vpc_id":        { "value": "..." },
#   "campaign_id":   { "value": "v0.7.0-r1" }
# }
```

## Destroy

```bash
terraform destroy \
  -var "do_token=$DIGITALOCEAN_TOKEN" \
  -var "ssh_key_fingerprint=$DIGITALOCEAN_SSH_KEY_FINGERPRINT" \
  -var "campaign_id=v0.7.0-r1"
```

(The 8-hour dead-man switch will also self-destroy any forgotten droplets.)
