# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
#
# v0.7.0 A2A campaign DigitalOcean fixture: TWO 16 GB AMD droplets joined by a
# campaign-scoped VPC. One droplet runs openclaw, the other hermes. Both run
# ai-memory v0.7.0 + audit feature on. The harness drives both via xAI/Grok 4.2.
#
# Apply with:
#   terraform init
#   terraform apply \
#     -var "do_token=$DIGITALOCEAN_TOKEN" \
#     -var "ssh_key_fingerprint=$DIGITALOCEAN_SSH_KEY_FINGERPRINT" \
#     -var "campaign_id=$CAMPAIGN_ID"
#
# Cost: ~$0.04/hr per droplet × 2 = ~$0.08/hr; ~$3 per round end-to-end.

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    digitalocean = {
      source  = "digitalocean/digitalocean"
      version = "~> 2.40"
    }
  }
}

provider "digitalocean" {
  token = var.do_token
}

locals {
  # DO tags accept only [a-z0-9:_-]; strip anything else from campaign_id.
  campaign_tag = replace(replace(var.campaign_id, ".", "-"), "/", "-")
  base_tags = [
    "ai-memory",
    "a2a-v07",
    "campaign-${local.campaign_tag}",
    "auto-destroy",
  ]

  cloud_init = templatefile("${path.module}/cloud-init.yaml", {
    campaign_id           = var.campaign_id
    dead_man_switch_hours = var.dead_man_switch_hours
  })
}

# Per-campaign VPC. Owning the VPC ourselves makes the plan deterministic and
# lets us tear it down cleanly. Range matches ship-gate's convention.
resource "digitalocean_vpc" "campaign" {
  name     = "a2a-v07-${local.campaign_tag}-vpc"
  region   = var.region
  ip_range = var.vpc_cidr
}

# openclaw-node : agent_id ai:openclaw@nyc3:droplet-1
resource "digitalocean_droplet" "openclaw" {
  image    = var.image
  name     = "a2a-v07-${local.campaign_tag}-openclaw"
  region   = var.region
  size     = var.droplet_size
  ssh_keys = [var.ssh_key_fingerprint]
  tags     = concat(local.base_tags, ["role-openclaw"])
  vpc_uuid = digitalocean_vpc.campaign.id

  user_data     = local.cloud_init
  droplet_agent = true
}

# hermes-node : agent_id ai:hermes@nyc3:droplet-2
resource "digitalocean_droplet" "hermes" {
  image    = var.image
  name     = "a2a-v07-${local.campaign_tag}-hermes"
  region   = var.region
  size     = var.droplet_size
  ssh_keys = [var.ssh_key_fingerprint]
  tags     = concat(local.base_tags, ["role-hermes"])
  vpc_uuid = digitalocean_vpc.campaign.id

  user_data     = local.cloud_init
  droplet_agent = true
}

# Cloud firewall: SSH open from runner; A2A port open ONLY between the two
# droplets in the VPC; everything else closed.
resource "digitalocean_firewall" "a2a" {
  name = "a2a-v07-${local.campaign_tag}-fw"
  tags = local.base_tags

  # SSH from anywhere (or restrict to GH Actions egress in production).
  inbound_rule {
    protocol         = "tcp"
    port_range       = "22"
    source_addresses = var.ssh_source_cidrs
  }

  # ai-memory A2A port — restricted to the peer's private VPC IP.
  # The two droplets see each other on their .ipv4_address_private addresses.
  inbound_rule {
    protocol = "tcp"
    port_range = "19077"
    source_addresses = [
      digitalocean_droplet.openclaw.ipv4_address_private,
      digitalocean_droplet.hermes.ipv4_address_private,
    ]
  }

  # Outbound: pull binary tarballs, hit xAI API, hit GitHub.
  outbound_rule {
    protocol              = "tcp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }
  outbound_rule {
    protocol              = "udp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }
  outbound_rule {
    protocol              = "icmp"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }
}
