# Container Apps Bicep and the Entra provisioning script

Deferred by ADR 0010; the hosting choice itself is ADR 0002 and the provisioning approach is
ADR 0003.

## Goal

One command provisions the deployed service from nothing, and a second deploys to it.

## Requirements

- **Bicep only, no Terraform.** ADR 0003 settles this and `scripts/check_structure.py` enforces
  it — `*.tf` and `*.tfvars` are rejected outright.
- Azure Container Apps for the service, Key Vault for secrets, a user-assigned managed identity
  with scoped RBAC role assignments.
- **An idempotent Graph script for the Entra objects, not Bicep.** ARM has no resource provider
  for app registrations, federated identity credentials, directory role assignments, or secret
  rotation, so that half cannot be expressed in a template. The script keys on `displayName` so a
  re-run converges, and writes generated secrets straight to Key Vault.
- Deploy triggered by a workflow using GitHub OIDC federated credentials (ADR 0004) — no stored
  cloud credential.

## Acceptance Criteria

- [ ] `bicep build` and a what-if run succeed against an empty resource group.
- [ ] The Entra script is genuinely idempotent: two consecutive runs produce identical state and
      the second creates nothing.
- [ ] No secret is ever written to a file in the repo or printed to a log.

## Notes

`infra/main.bicep` already exists and covers part of this. Start by reading it rather than
starting over.
