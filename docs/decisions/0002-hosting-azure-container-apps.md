---
title: "ADR 0002: Hosting for the deployed service is Azure Container Apps"
type: adr
permalink: adr-0002-hosting-azure-container-apps
tags:
  - adr
---

# ADR 0002 — Hosting for the deployed service is Azure Container Apps

**Status:** Accepted — deferred (2026-08-05). Deferred by ADR 0010; the deployed multi-user
service is not being built in this consolidation. This ADR fixes the target so the deferred infra
work has one, rather than being re-litigated when someone picks it up.

## Context

The repository already carries a deployable shape: a containerised web application, Bicep for
Storage, ACR, PostgreSQL Flexible Server, Key Vault, Log Analytics, and an App Service plan plus
site — the last of which came from a dev-era deployment that ran end to end.

The service, when it ships, is one long-lived web process plus background workers that wake on an
interval. It needs container images (they already exist), scale-to-low, managed identity, and
private access to Postgres and Key Vault. It does not need a cluster, a service mesh, or a
scheduler beyond "keep N replicas alive".

## Decision

Azure Container Apps, when the deployed service is built.

The App Service resources currently in `infra/main.bicep` are dev-era artifacts. They are
superseded when the Container Apps Bicep lands; they are not maintained as a second supported
target in the meantime.

## Consequences

- The Container Apps Bicep is a backlog item in this repository, sequenced with the rest of the
  deferred service work (ADR 0010).
- The container build and the image registry are already in place, so the deferred work is a
  hosting resource and its identity wiring, not a packaging project.
- ARM resources are Bicep. Entra objects are not ARM resources and are handled separately by
  ADR 0003; the deploy trigger by ADR 0004.
- Nothing in the library depends on this choice. Hosting is reachable only through the deferred
  adapters, so reversing this ADR costs one Bicep file and no application code.
