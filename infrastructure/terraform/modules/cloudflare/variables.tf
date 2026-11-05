variable "account_id" {
  type = string
}

variable "domain" {
  type = string
}

variable "enable_default_firewall_rules" {
  type    = bool
  default = true
}

variable "dns_entries" {
  type = list(object({
    id       = optional(string)
    name     = string,
    value    = string,
    type     = optional(string, "A"),
    proxied  = optional(bool, true),
    priority = optional(number, 0),
    ttl      = optional(number, 1)
  }))
  default = []
}

variable "waf_custom_rules" {
  type = list(object({
    enabled           = bool
    description       = string
    expression        = string
    action            = string
    action_parameters = optional(any, {})
    logging           = optional(any, {})
  }))
  default = []
}

variable "tunnel_id" {
  description = "cloudflared tunnel ID that ingress.<domain> points at (tunnel-dns.tf). Needed when tunnel_public_hosts is set."
  type        = string
  default     = null
  sensitive   = true
}
variable "tunnel_public_hosts" {
  description = "Public app names (keys) served through the tunnel, each a proxied CNAME to ingress.<domain>."
  type        = set(string)
  default     = []
}
variable "tunnel_host_names" {
  description = "Real names for keys in tunnel_public_hosts whose name is a secret (key -> name)."
  type        = map(string)
  default     = {}
  sensitive   = true
}
