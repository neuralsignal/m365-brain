---
title: "ADR 0003: Entra provisioning is an idempotent Graph script; Bicep for ARM, no Terraform"
type: adr
permalink: adr-0003-entra-provisioning-idempotent-graph-script
tags:
  - adr
---

# ADR 0003 — Entra provisioning is an idempotent Graph script; Bicep for ARM, no Terraform

**Status:** Accepted — deferred (2026-08-05). The provisioning script itself is deferred by ADR
0010 along with the rest of the deployed service. The no-Terraform half is in force now and is
mechanically enforced.

## Context

The deployed service needs Entra objects that ARM has no resource provider for: application
registrations and their service principals, and federated identity credentials for the deploy
workflow (ADR 0004). Bicep cannot create them. The codebase being retired created them with
Terraform — roughly 200 lines under an `infra/entra/` root — alongside a second Terraform root for
the Azure resources.

Adopting Terraform for that alone means a second IaC toolchain, a second state store to host and
back up, a second set of credentials in CI, and a second thing every contributor must install —
for a handful of objects that are created once per environment and then edited by hand in the
portal anyway. Meanwhile the ARM half is already Bicep, and duplicating it in Terraform to keep
one toolchain would throw away working infrastructure code.

## Decision

- **Entra objects** are created by an idempotent Python script against Microsoft Graph, keyed on
  `displayName` so re-running converges instead of duplicating. Generated secrets are written to
  Key Vault, never to a state file and never to the repository.
- **ARM resources** stay in Bicep.
- **Terraform is not adopted, in any form.** `scripts/check_structure.py` rejects `*.tf` and
  `*.tfvars` anywhere in the tree.

## Consequences

- One IaC toolchain, no state store, no state-file secret handling.
- Idempotency is the script's whole correctness property, since there is no state file to diff
  against. Keying on `displayName` is the mechanism, and a rename upstream therefore looks like a
  new object — an accepted sharp edge, documented at the call site rather than engineered around.
- The federated identity credential ADR 0004 depends on is one of the objects this script creates,
  so provisioning runs before the first deploy.
- The check is a ratchet: the ban survives even if a future contributor finds a Terraform provider
  convenient, because reversing it requires deleting a rule rather than adding a file.
