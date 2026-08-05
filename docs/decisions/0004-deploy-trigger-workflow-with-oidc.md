---
title: "ADR 0004: Deploys are triggered by a workflow using OIDC federated credentials"
type: adr
permalink: adr-0004-deploy-trigger-workflow-with-oidc
tags:
  - adr
---

# ADR 0004 — Deploys are triggered by a workflow using OIDC federated credentials

**Status:** Accepted — deferred (2026-08-05). Deferred by ADR 0010 together with the deployed
service; there is nothing to deploy until the service exists.

## Context

Deploying needs a trigger and a credential. The repository already runs its CI, release
automation, and PyPI publishing in GitHub Actions, and already publishes to PyPI with an OIDC
trusted publisher — so the mechanism is in use and understood here.

The alternative credential is a long-lived Azure service-principal secret stored in repository
secrets. It works, and it is a standing credential with a rotation obligation nobody schedules,
readable by anything that can run a workflow.

## Decision

Deploys are triggered by a GitHub Actions workflow, authenticating to Azure with OIDC federated
identity credentials. No long-lived Azure secret is stored in the repository.

## Consequences

- The federated identity credentials — one subject per protected ref — are Entra objects, so they
  are created by ADR 0003's provisioning script. Provisioning precedes the first deploy.
- Deploy permissions are scoped by the federated subject rather than by who holds a secret,
  so a credential cannot be lifted out of the repository and used elsewhere.
- Nothing to rotate, nothing to expire silently.
- The workflow is a backlog item alongside the Container Apps Bicep (ADR 0002); this ADR only
  fixes the mechanism so that work does not start by picking one.
