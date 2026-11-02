## ---------------------------------------------------------------------------------------------------------------------
## VARIABLES
## ---------------------------------------------------------------------------------------------------------------------

variable "unifi_url" {
  description = "Controller URL, without /api. Overrides unifi_url in secrets.sops.yaml (e.g. the MetalLB VIP while the ingress is down)."
  type        = string
  default     = null
}
