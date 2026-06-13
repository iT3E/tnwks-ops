terraform {
  cloud {
    hostname     = "app.terraform.io"
    organization = "tnwks-ops"
    workspaces {
      name = "tnwks-cloudflare-prod_old"
    }
  }
  required_version = ">= 1.2.2"
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "5.20.0"
    }
    sops = {
      source  = "carlpett/sops"
      version = "1.4.1"
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
  source     = "../modules/cloudflare"
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
      name    = "ys7i3eaaalsfrw2i4rsp3xldunp2tolm._domainkey.tnwks.us"
      value   = "ys7i3eaaalsfrw2i4rsp3xldunp2tolm.dkim.amazonses.com"
      type    = "CNAME"
    },
    {
      id      = "dkim2"
      proxied = false
      name    = "c6sfao5fu2qj7nvqldbg3xiolwrcvciv._domainkey.tnwks.us"
      value   = "c6sfao5fu2qj7nvqldbg3xiolwrcvciv.dkim.amazonses.com"
      type    = "CNAME"
    },
    {
      id      = "dkim3"
      proxied = false
      name    = "63kkuuq7bcccxechn2ogtp7ts7beltmy._domainkey.tnwks.us"
      value   = "63kkuuq7bcccxechn2ogtp7ts7beltmy.dkim.amazonses.com"
      type    = "CNAME"
    },
    # Apex placeholder A record. Cognito custom-domain creation refuses to
    # proceed when the parent zone has no A record at the apex; a documentation
    # IP (RFC 5737) satisfies the check without conflicting with anything.
    {
      id      = "apex_placeholder"
      name    = data.sops_file.secrets.data["cf_domain_1"]
      value   = "192.0.2.1"
      type    = "A"
      proxied = false
    },
    # ACM DNS validation for the auth.tnwks.us cert (us-east-1) backing the
    # Cognito custom domain. Validation values were emitted by the prod AWS
    # workspace as auth_acm_validation_records.
    {
      id      = "acm_auth_tnwks_validation"
      name    = "_5e54535c3306e92b67d89fc23dc5cc03.auth.tnwks.us"
      value   = "_1437828207dd71206b7612bed3acfdaf.jkddzztszm.acm-validations.aws"
      type    = "CNAME"
      proxied = false
    },
    # auth.tnwks.us → Cognito custom-domain CloudFront distribution. Value
    # comes from the prod AWS workspace output
    # cognito_custom_domain_cloudfront_distribution. Must not be proxied:
    # Cloudflare's proxy would terminate TLS with the wrong cert (Cloudflare's,
    # not the auth.tnwks.us ACM cert ATTACHED to the CloudFront distro).
    {
      id      = "cognito_custom_domain"
      name    = "auth.tnwks.us"
      value   = "d2sskyx0g75op2.cloudfront.net"
      type    = "CNAME"
      proxied = false
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
  source     = "../modules/cloudflare"
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
  source     = "../modules/cloudflare"
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
  source     = "../modules/cloudflare"
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
