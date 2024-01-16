##### Prerequisite:  `aws configure sso --profile default`


1. `aws sso login --profile default`

          ```
          # ---------------------------------------------------------------------------------------------
          # https://old.reddit.com/r/aws/comments/zk456d/new_aws_cli_and_sso_sessions_profiles_and_legacy/
          # SOPS does not know how to use the "new" (2022) sso-session, so it needs legacy login.  This is
          # simply running `aws configure sso` and then leaving SSO Session Name blank.
          # ---------------------------------------------------------------------------------------------
          ```
2. `sops -e -i secrets.sops.yaml`


- you may have to go to `C:\Users\<your_user>\.aws` and remove all contents from `credentials` file
