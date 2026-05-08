# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0

output "openclaw_node" {
  description = "Public + private IPs of openclaw-node (agent_id ai:openclaw@nyc3:droplet-1)."
  value = {
    public  = digitalocean_droplet.openclaw.ipv4_address
    private = digitalocean_droplet.openclaw.ipv4_address_private
  }
}

output "hermes_node" {
  description = "Public + private IPs of hermes-node (agent_id ai:hermes@nyc3:droplet-2)."
  value = {
    public  = digitalocean_droplet.hermes.ipv4_address
    private = digitalocean_droplet.hermes.ipv4_address_private
  }
}

output "vpc_id" {
  description = "Campaign-scoped VPC UUID."
  value       = digitalocean_vpc.campaign.id
}

output "campaign_id" {
  description = "Echo of the campaign_id input."
  value       = var.campaign_id
}
