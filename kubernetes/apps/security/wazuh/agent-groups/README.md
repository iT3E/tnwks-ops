# Wazuh agent rollout

The Ansible Hosts workflow manages agents on hosts that set
`windows_wazuh_rollout_name` or `wsl_wazuh_rollout_name` in their inventory
host variables. Each role requires a play limited to one endpoint of its
type. The operator playbook also
requires a one-host limit and an explicit matching `wazuh_rollout_host`.

The Windows and amd64 Debian packages match the
[Wazuh 5.0.0 RC2 artifact manifest](https://packages-staging.xdrsiem.wazuh.info/pre-release/5.x/artifact-urls/artifact_urls_5.0.0-rc2.yaml).
Both roles check the deployed artifact's SHA-256 before installation and
record the package pin. Review and rehearse an upgrade separately. A role
refuses an existing agent with a different or missing package record.

Before an enrollment, review the selected group file. Both Windows hosts
use `windows-host`. The Windows group enables CIS assessment and records
inventory, file metadata and hashes, and selected Security and Defender
events. The agent can record process command lines and detection file paths.
The distro group records its inventory, file integrity, and SSH events.
Both configurations disable active response.

## Approved enrollment window

1. The operator opens a local-only port-forward to the manager API Service
   on the server cluster. Use port 15500 and the API's public CA certificate.
2. The operator runs `prepare-enrollment.py` with the chosen group, the
   agent-facing manager DNS name, the CA path, and a new file in a private
   directory as its token output. The script prompts for the manager API
   account's password, publishes the group configuration from this checkout,
   and requests a one-use token valid for one hour.
3. For an approved GitOps rollout, put the agent alias in the
   host's inventory and the one-use token in its SOPS host variables as
   `windows_wazuh_agent_enrollment_token` or
   `wsl_wazuh_agent_enrollment_token`. The PR check inspects the host.
   The apply after merge consumes the token on first enrollment.
   For a rollout using `infrastructure/ansible/playbooks/wazuh-agents.yml`,
   pass the token file's absolute path as `wazuh_agent_token_file`, the
   approved inventory name as `wazuh_rollout_host` and through `--limit`,
   and the endpoint alias as `wazuh_agent_name`. Run from the pinned
   Ansible controller. Both paths use `wazuh-agent-auth`.
4. Remove the token from the operator's controller after the attempt.
   Verify the enrolled name and group and that the agent reports. For an
   interrupted enrollment, inspect the manager's registration before
   minting a replacement token.

Before it starts a service, the rollout play verifies that its local
configuration disables active response and all three remote-command options.

To roll back an endpoint enrolled through GitOps, remove the host opt-in
through a PR before removing the package. To remove the package, run the
one-host operator play with
`wazuh_agent_state=absent`. This removes the verified package without a
token. Remove its registered identity through the approved manager
procedure after the package is gone.

See the [pinned manager API](https://github.com/wazuh/wazuh/blob/v5.0.0-rc2/api/api/spec/spec.yaml) and [pinned installation guide](https://github.com/wazuh/wazuh/blob/v5.0.0-rc2/docs/ref/getting-started/installation.md).
