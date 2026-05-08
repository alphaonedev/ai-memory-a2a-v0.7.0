# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0

variable "do_token" {
  description = "DigitalOcean API token. Read from DIGITALOCEAN_TOKEN env."
  type        = string
  sensitive   = true
}

variable "ssh_key_fingerprint" {
  description = "SHA-256 fingerprint of an SSH key already registered with DO."
  type        = string
  default     = "bf:0b:e1:92:6f:ea:0d:1d:e4:96:ee:ac:71:73:ed:4e"
}

variable "ssh_source_cidrs" {
  description = "CIDRs allowed to ssh in. Tighten to GH Actions IPs in production."
  type        = list(string)
  default     = ["0.0.0.0/0", "::/0"]
}

variable "region" {
  description = "DigitalOcean region."
  type        = string
  default     = "nyc3"
}

variable "image" {
  description = "DO droplet image. Matches ship-gate baseline."
  type        = string
  default     = "ubuntu-24-04-x64"
}

variable "droplet_size" {
  description = "Droplet size for openclaw + hermes nodes."
  type        = string
  # 16 GB AMD: openclaw needs >8 GB resident; hermes mirrors for symmetry.
  default = "s-4vcpu-16gb-amd"
}

variable "vpc_cidr" {
  description = "VPC CIDR for the campaign-scoped VPC."
  type        = string
  default     = "10.250.0.0/20"
}

variable "campaign_id" {
  description = "Opaque identifier for this campaign run. Becomes part of resource names + the runs/<id>/ directory."
  type        = string
}

variable "dead_man_switch_hours" {
  description = "Drops self-destruct after this many hours regardless. 8h matches ship-gate."
  type        = number
  default     = 8
}
