module "iam_role_itadmin" {
  source             = "../../../../modules/aws/iam/roles"
  name               = "itadmin_role"
}
