---
title: "Design: GitHub Actions Integration"
status: draft
date: 2026-03-08
author: Matthias Christenson
---

# GitHub Actions Integration

## Context

Knowledge bases benefit from CI/CD: automated sync, validation, and search index maintenance. This document specifies GitHub Actions composite actions and reusable workflows so users can keep their knowledge base indexed and validated in CI.

This also enables teams where multiple people contribute to a shared knowledge base — CI ensures the index stays current and validates markdown files on every PR. The design aligns with the dark factory patterns from the agentic-engineering-kit, which provides battle-tested autonomous CI/CD workflows using Claude Code.

## Scope for v0.1

**In scope:**

- Composite actions: `m365-brain/setup`, `m365-brain/sync`, `m365-brain/validate`
- Reusable workflow: scheduled sync with artifact upload
- Database persistence between CI runs (cache strategy)
- Connector authentication in CI (secrets)
- Relationship to `anthropics/claude-code-action`

**Out of scope:**

- Self-hosted runner configurations
- Non-GitHub CI systems (GitLab, CircleCI) — can be adapted from the workflow patterns
- Automated knowledge updates via agents in CI

### Dark Factory Integration

The agentic-engineering-kit provides a "dark factory" — a fully autonomous CI/CD pipeline using GitHub Actions + `anthropics/claude-code-action@v1`. This package's CI/CD adopts the same patterns:

**Assessment agents (scheduled):**

| Agent | Schedule | Purpose |
|-------|----------|---------|
| dep-audit | Weekly | Find outdated deps, vulnerabilities |
| security-scan | Weekly | Find security issues |
| code-quality | Weekly | Find complexity, duplication |
| test-coverage | Weekly | Find uncovered code |
| docs-freshness | Monthly | Find stale docs |

**Reactive agents (event-triggered):**

| Agent | Trigger | Purpose |
|-------|---------|---------|
| issue-triage | Issue opened | Classify and label |
| issue-implement | `claude:implement` label | Implement as PR |
| pr-code-review | PR opened/synced | Auto code review |
| pr-docs-check | PR opened/synced | Documentation compliance |
| factory-orchestrator | Hourly | Sweep orphaned issues, retry blocked |

**Core design patterns:**

1. **CLAUDE.md as single customization surface** — all project-specific config lives in CLAUDE.md
2. **File-based output** — Claude output to a temp `.md` file, posted via `--body-file` (no shell escaping)
3. **Dedup via labels** — each agent tags issues with `source:<agent>` and checks before creating duplicates
4. **GITHUB_TOKEN cascade fix** — the factory orchestrator uses a separate PAT to re-label orphaned issues
5. **Concurrency groups** — one implementation per issue at a time
6. **No auto-merge** — agents create PRs, humans merge

## Design

### Composite Actions

#### `m365-brain/setup`

Install the package and optionally configure extras.

```yaml
# .github/actions/m365-brain-setup/action.yml
name: "Setup m365-brain"
description: "Install m365-brain with specified extras"
inputs:
  python-version:
    description: "Python version to use"
    required: true
  extras:
    description: "Comma-separated extras to install"
    required: false
    default: ""
  version:
    description: "Package version constraint"
    required: false
    default: ""
runs:
  using: composite
  steps:
    - uses: actions/setup-python@v5
      with:
        python-version: ${{ inputs.python-version }}

    - name: Install m365-brain
      shell: bash
      run: |
        if [ -n "${{ inputs.extras }}" ]; then
          pip install "m365-brain[${{ inputs.extras }}]${{ inputs.version }}"
        else
          pip install "m365-brain${{ inputs.version }}"
        fi

    - name: Verify installation
      shell: bash
      run: m365-brain --version
```

#### `m365-brain/sync`

Run the index sync with database caching.

```yaml
# .github/actions/m365-brain-sync/action.yml
name: "Sync knowledge index"
description: "Index markdown files into the SQLite database"
inputs:
  full-rebuild:
    description: "Force full rebuild"
    required: false
    default: "false"
  rebuild-vectors:
    description: "Rebuild vector embeddings"
    required: false
    default: "false"
outputs:
  entities:
    description: "Number of entities synced"
  database-path:
    description: "Path to the SQLite database"
runs:
  using: composite
  steps:
    - name: Restore database cache
      uses: actions/cache@v4
      with:
        path: _meta/knowledge.db
        key: m365-brain-index-${{ hashFiles('vault/**/*.md') }}
        restore-keys: m365-brain-index-

    - name: Sync
      shell: bash
      run: |
        ARGS=""
        if [ "${{ inputs.full-rebuild }}" = "true" ]; then
          ARGS="$ARGS --full-rebuild"
        fi
        if [ "${{ inputs.rebuild-vectors }}" = "true" ]; then
          ARGS="$ARGS --rebuild-vectors"
        fi
        m365-brain index sync $ARGS --verbose
```

The `path` and `hashFiles` glob above track the configured index roots and meta directory; a workspace that configures different names changes them here too.

#### `m365-brain/validate`

Run validation and report issues.

```yaml
# .github/actions/m365-brain-validate/action.yml
name: "Validate knowledge base"
description: "Check markdown files against schemas"
inputs:
  strict:
    description: "Treat warnings as errors"
    required: false
    default: "false"
runs:
  using: composite
  steps:
    - name: Validate
      shell: bash
      run: |
        ARGS=""
        if [ "${{ inputs.strict }}" = "true" ]; then
          ARGS="$ARGS --strict"
        fi
        m365-brain validate $ARGS --format json > validation-results.json
        m365-brain validate $ARGS

    - name: Upload results
      if: always()
      uses: actions/upload-artifact@v4
      with:
        name: validation-results
        path: validation-results.json
```

### Reusable Workflow: Scheduled Sync

```yaml
# .github/workflows/knowledge-sync.yml
name: Knowledge Base Sync
on:
  push:
    paths:
      - 'vault/**'
  schedule:
    - cron: '0 6 * * *'  # daily at 06:00 UTC
  workflow_dispatch:

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: ./.github/actions/m365-brain-setup
        with:
          python-version: "3.12"
          extras: "vector"

      - uses: ./.github/actions/m365-brain-sync
        with:
          rebuild-vectors: "true"

      - uses: ./.github/actions/m365-brain-validate

      - name: Upload database artifact
        uses: actions/upload-artifact@v4
        with:
          name: knowledge-db
          path: _meta/knowledge.db
          retention-days: 30
```

### Database Persistence Strategy

The SQLite database is a derived artifact (rebuilt from markdown files). Persistence options:

| Strategy | Pros | Cons |
|----------|------|------|
| **Cache** (actions/cache) | Fast restore, hash-based invalidation | Cache eviction possible, 10 GB limit |
| **Artifact** (actions/upload-artifact) | Reliable, downloadable, versioned | Slower, costs artifact storage |
| **Commit to repo** | Always available | Pollutes git history, merge conflicts |
| **Rebuild from scratch** | Always correct | Slow for large knowledge bases |

**Recommended:** Cache with artifact fallback. Cache keyed on a hash of the indexed markdown for fast incremental builds. Artifact uploaded after each sync for reliable backup.

### Connector Authentication in CI

Cloud connectors (if configured) need credentials in CI:

```yaml
# Example: S3 connector
env:
  AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
  AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}

# Example: Google Drive connector
steps:
  - name: Write credentials
    run: echo '${{ secrets.GOOGLE_CREDENTIALS }}' > /tmp/creds.json
    env:
      M365_BRAIN_CONNECTORS_GDRIVE_CREDENTIALS: /tmp/creds.json
```

Each connector plugin documents its CI authentication requirements.

### Relationship to claude-code-action

[`anthropics/claude-code-action`](https://github.com/anthropics/claude-code-action) enables Claude Code in GitHub Actions for PR review, code generation, etc. This package's GitHub Actions complement it:

1. **Index sync in CI** ensures the knowledge database is available when Claude Code runs
2. **Validation in PR checks** ensures markdown files in PRs are schema-compliant
3. **Claude Code + the CLI** — when Claude Code runs in a workflow, it can use `index search` and `index context` to inform its responses (if the search-first rule is installed)

These are independent tools that compose well together.

### PR Validation Workflow

A workflow that validates markdown files on every PR:

```yaml
# .github/workflows/knowledge-validate-pr.yml
name: Validate Knowledge PR
on:
  pull_request:
    paths:
      - 'vault/**'

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: ./.github/actions/m365-brain-setup
        with:
          python-version: "3.12"

      - uses: ./.github/actions/m365-brain-sync

      - uses: ./.github/actions/m365-brain-validate
        with:
          strict: "true"
```

### CI Workflow (this repository)

Standard CI for the package itself:

```yaml
# .github/workflows/ci.yml
name: CI
on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: prefix-dev/setup-pixi@v0.8.8
      - name: Install
        run: pixi install
      - name: Lint
        run: pixi run lint
      - name: Structure
        run: python3 scripts/check_structure.py
      - name: Test
        run: pixi run test
```

The structure step is not decoration: the layering rules in `02-package-architecture.md` and the consumer-vocabulary ban are enforced by scripts (`check_structure.py`, `check_no_workspace.py`), and CI is where they bite.

### Release Workflow

Automated PyPI publishing on tagged releases:

```yaml
# .github/workflows/release.yml
name: Release
on:
  push:
    tags: ["v*"]

jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      id-token: write  # trusted publishing
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Build
        run: pip install build && python -m build
      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1
```

## Decisions

No new decision IDs for this document. The GitHub Actions design follows directly from the CLI design (D-20, D-21) and the configuration system (D-09, D-10).

## Resolved Questions

1. **Action versioning** — Composite actions live in this repository. Dark factory workflows are copied from the agentic-engineering-kit.
2. **PR comment integration** — Not in v0.1. Dark factory's `pr-code-review` handles PR feedback.
3. **Matrix testing** — CI tests Python 3.11, 3.12, 3.13 on ubuntu-latest. (The package as shipped requires >=3.12.)

## Open Questions

1. **Database download action** — Deferred to v0.2. Should there be a download-index action for cross-workflow DB access?

## References

- GitHub Actions composite actions: <https://docs.github.com/en/actions/sharing-automations/creating-actions/creating-a-composite-action>
- GitHub Actions reusable workflows: <https://docs.github.com/en/actions/sharing-automations/reusing-workflows>
- actions/cache: <https://github.com/actions/cache>
- anthropics/claude-code-action: <https://github.com/anthropics/claude-code-action>
- agentic-engineering-kit dark factory: <https://github.com/anthropics/agentic-engineering-kit>
