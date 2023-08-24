terraform {
  cloud {
    hostname     = "app.terraform.io"
    organization = "tnwks-ops"
  workspaces {
    name = "tnwks-cloudflare-prod"
  }
}
    required_version = ">= 1.2.2"
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "4.13.0"
   }
    sops = {
      source  = "carlpett/sops"
      version = "0.7.2"
  }
 }
}

data "sops_file" "secrets" {
  source_file = "secrets.sops.yaml"
}

#########################
#########################

#####################################
##                                 ##
##           cf_domain_1           ##
##                                 ##
#####################################

module "cf_domain_1" {
  source     = "../../../modules/cloudflare"
  domain     = data.sops_file.secrets.data["cf_domain_1"]
  account_id = data.sops_file.secrets.data["cf_account_id"]

  dns_entries = [
    # Generic settings
    {
      name  = "_dmarc"
      value = "v=DMARC1; p=quarantine;"
      type  = "TXT"
    },
    # SES settings
    {
      id       = "ses_mx_1"
      name     = "mail"
      priority = 10
      value    = "feedback-smtp.us-west-2.amazonses.com"
      type     = "MX"
    },
    {
      id       = "ses_mx_2"
      name     = data.sops_file.secrets.data["cf_domain_1"]
      priority = 10
      value    = "inbound-smtp.us-west-2.amazonaws.com"
      type     = "MX"
    },
    {
      id    = "ses_validate"
      name  = "_amazonses"
      value = "V/1P5rhPpHNwnye71w8WxzjCmFyL7KDapKFmVY8nbiA="
      type  = "TXT"
    },
    {
      id    = "ses_spf"
      name  = "mail"
      value = "v=spf1 include:amazonses.com ~all"
      type  = "TXT"
    },
    {
      id    = "unms"
      name  = "unms"
      value = data.sops_file.secrets.data["cf_domain_1_aws_eip_unms"]
      type  = "A"
    },
    {
      name  = "ipv4"
      value = data.sops_file.secrets.data["cf_domain_1"]
      type  = "CNAME"
    },
    {
      id      = "dkim1"
      proxied = false
      name    = "2t67hhkulgkcugrm3l244dba4q3d3bwj._domainkey"
      value   = "2t67hhkulgkcugrm3l244dba4q3d3bwj.dkim.amazonses.com"
      type    = "CNAME"
    },
    {
      id      = "dkim2"
      proxied = false
      name    = "ylx2z2nwy4jlyh5bsb26jfrvkoazaxha._domainkey"
      value   = "ylx2z2nwy4jlyh5bsb26jfrvkoazaxha.dkim.amazonses.com"
      type    = "CNAME"
    },
    {
      id      = "dkim3"
      proxied = false
      name    = "u6egebawhot4h7vilecdj2egi7jsggar._domainkey"
      value   = "u6egebawhot4h7vilecdj2egi7jsggar.dkim.amazonses.com"
      type    = "CNAME"
    },
  ]

  waf_custom_rules = [
    {
      enabled     = true
      description = "Firewall rule to block bots and threats determined by CF"
      expression  = "(cf.client.bot) or (cf.threat_score gt 14)"
      action      = "block"
    },
    {
      enabled     = true
      description = "Firewall rule to block all countries except US"
      expression  = "(ip.geoip.country ne \"US\")"
      action      = "block"
    },
  ]
}

#####################################
##                                 ##
##           cf_domain_2           ##
##                                 ##
#####################################

module "cf_domain_2" {
  source     = "../../../modules/cloudflare"
  domain     = data.sops_file.secrets.data["cf_domain_2"]
  account_id = data.sops_file.secrets.data["cf_account_id"]

  dns_entries = [
    # WIP
    {
      name  = data.sops_file.secrets.data["cf_domain_2"]
      value = data.sops_file.secrets.data["cf_domain_1"]
      type  = "CNAME"
    },
  ]

  waf_custom_rules = [
    {
      enabled     = true
      description = "Firewall rule to block bots and threats determined by CF"
      expression  = "(cf.client.bot) or (cf.threat_score gt 14)"
      action      = "block"
    },
    {
      enabled     = true
      description = "Firewall rule to block all countries except US"
      expression  = "(ip.geoip.country ne \"US\")"
      action      = "block"
    },
  ]
}


#####################################
##                                 ##
##           cf_domain_3           ##
##                                 ##
#####################################

module "cf_domain_3" {
  source     = "../../../modules/cloudflare"
  domain     = data.sops_file.secrets.data["cf_domain_3"]
  account_id = data.sops_file.secrets.data["cf_account_id"]

  dns_entries = [
    # Generic settings
    {
      name  = data.sops_file.secrets.data["cf_domain_3"]
      value = data.sops_file.secrets.data["cf_domain_3_cloudfront"]
      type  = "CNAME"
    },
  ]

  waf_custom_rules = [
    {
      enabled     = true
      description = "Firewall rule to block bots and threats determined by CF"
      expression  = "(cf.client.bot) or (cf.threat_score gt 14)"
      action      = "block"
    },
    {
      enabled     = true
      description = "Firewall rule to block all countries except US"
      expression  = "(ip.geoip.country ne \"US\")"
      action      = "block"
    },
  ]
}

#####################################
##                                 ##
##           cf_domain_4           ##
##                                 ##
#####################################

module "cf_domain_4" {
  source     = "../../../modules/cloudflare"
  domain     = data.sops_file.secrets.data["cf_domain_4"]
  account_id = data.sops_file.secrets.data["cf_account_id"]

  dns_entries = [
    # Generic settings
    {
      name  = "status"
      value = "ingress.${data.sops_file.secrets.data["cf_domain_1"]}"
      type  = "CNAME"
    },
  ]

  waf_custom_rules = [
    {
      enabled     = true
      description = "Firewall rule to block bots and threats determined by CF"
      expression  = "(cf.client.bot) or (cf.threat_score gt 14)"
      action      = "block"
    },
    {
      enabled     = true
      description = "Firewall rule to block all countries except US"
      expression  = "(ip.geoip.country ne \"US\")"
      action      = "block"
    },
  ]
}
