# m365-extract

Microsoft 365 data extraction to Obsidian-compatible markdown via Graph API.

## Repo Structure

| Path | Purpose |
|------|---------|
| `m365_extract/` | Source package |
| `m365_extract/auth/` | MSAL device code + auth code flow, token provider |
| `m365_extract/models.py` | SQLModel tables shared between admin UI and daemon |
| `m365_extract/cli.py` | Click CLI (`auth login`, `sync --once/--continuous`) |
| `m365_extract/sync.py` | Public sync API (extractor runner, used by CLI + daemon) |
| `m365_extract/config/` | Frozen dataclass config loader with env var expansion |
| `m365_extract/converters/` | Document conversion (obsidian-import wrapper, html_to_md) |
| `m365_extract/extractors/` | 8 extractors: email, calendar, teams_chats, teams_channels, onedrive, sharepoint, contacts, directory |
| `m365_extract/graph_client.py` | Microsoft Graph API client (httpx, pagination, retry, rate limiting) |
| `m365_extract/markdown_writer.py` | Markdown frontmatter builders + slugify |
| `m365_extract/logging_config.py` | Central structlog configuration (`configure_logging()`) |
| `m365_extract/state.py` | Sync state persistence (delta tokens, atomic writes) |
| `m365_extract/storage/` | StorageBackend protocol, local filesystem, Azure Blob Storage |
| `m365_admin/` | Reflex admin dashboard (OAuth, preferences, admin, sync status) |
| `m365_admin/auth_state.py` | Entra OAuth2 flow via Reflex state |
| `m365_admin/services/` | TokenService (Fernet), AdminService (config CRUD) |
| `m365_admin/pages/` | Login, callback, dashboard, settings, admin pages |
| `m365_admin/components/` | Sidebar, layout wrapper |
| `tests/` | 369 tests (pytest + hypothesis) |
| `infra/` | Bicep IaC (main.bicep, params.dev.bicepparam, params.prod.bicepparam) |
| `scripts/` | Dev setup, deploy, teardown scripts |
| `vault/` | Local sync output directory (emails, calendar, onedrive, sharepoint, teams-chats) |
| `pixi.toml` | pixi package manager config |
| `pyproject.toml` | Python package metadata |

## Logging

structlog is the only logging mechanism. No `click.echo`, `print`, or stdlib `logging` for operational output.

- **Central config**: `m365_extract/logging_config.py` — call `configure_logging(log_level, json_output)` once at startup
- **Entry points**: `cli.py:sync()` and `web/app.py:create_app()` call `configure_logging()` before any log output
- **Event naming**: `module.action` (e.g., `email.sync_complete`, `graph.retryable_error`, `cli.dry_run_auth_ok`)
- **Renderer**: JSON when `service.json_logs: true` (daemon/production), ConsoleRenderer when false (dev/interactive)
- **Log levels**: debug (pagination, internal state), info (sync lifecycle), warning (skipped items, truncation), error (failures that continue), critical (daemon exit)
- **Exception**: `click.echo` is acceptable in `auth login` and `auth status` commands (user-facing interactive output, not operational logging)

## Engineering Standards

- Follow KISS, DRY, YAGNI, fail fast principles
- No default arguments in function signatures
- All runtime values from config
- Custom exception classes (no bare Exception)
- Type hints on all functions
- pytest + hypothesis for testing
- ruff for linting and formatting

## Engineering Constitution

Non-negotiable principles. Violating these is a bug.

- **KISS** -- One tool per job. No clever abstractions, no premature generalization.
- **DRY** -- One source of truth. Shared logic in shared modules. Copy-paste is a defect.
- **No Default Arguments** -- Every value from config or caller. Defaults are hidden assumptions.
- **Fail Fast and Loud** -- No silent swallowing. No try-except-pass. Errors propagate with context.
- **Everything From Config** -- Intervals, paths, feature flags in config files. Code reads config, never invents values.
- **Modular and Independent** -- Each module standalone. No god objects, no shared mutable state.
- **No sys.path Manipulation** -- Use proper packaging via pyproject.toml. Import hacks are violations.
- **Descriptive Package Names** -- Never `src`, `lib`, `utils`, or `core` as importable names.
- **TDD** -- Write failing tests first, then implement. All tests via pytest.
- **Property-Based Testing** -- Use hypothesis for pure functions: parsers, transformers, validators.
- **Strict Typing** -- Type hints for every function argument and return value.
- **Custom Exceptions** -- No bare `Exception`. Use project-specific exception classes with context.
- **Composition Over Inheritance** -- Prefer small composable parts to deep inheritance chains.
- **Thin CLI Wrappers** -- CLI entry points are thin; business logic lives in library modules.
- **YAGNI** -- Do not add capabilities, config keys, feature flags, abstractions, or code paths without a concrete, current use case. Speculative "future-proof" code is a maintenance liability.
- **Idempotency** -- All scripts and operations must be re-runnable without side effects. Interrupted + restarted = same result as one clean run.

### Error Handling
- Every catch block: log, re-raise, or correct. Never catch-and-ignore.
- Error messages include: what failed, with what input, what the caller should do.
- Use custom exceptions (e.g., `GraphApiError`, `ConfigError`, `ExtractorError`).

### Architecture Patterns
- **Explicit Interfaces** -- Every module boundary has typed inputs and typed outputs. No implicit coupling through globals or shared mutable state.
- **Inward Dependency Direction** -- High-level extractors do not import from low-level implementation details. One extractor does not import from another.
- **Layer Separation** -- CLI layer calls library layer. Library layer calls I/O and third-party. No cross-layer skipping.
- **Flat Hierarchies** -- Prefer shallow package structures. Deep nesting obscures relationships and makes imports fragile.
- **No Narration Comments** -- Comments explain *why*, not *what*. Code that requires a comment to explain *what* it does should be rewritten to be self-explanatory.

### Dependency Discipline
- Prefer existing libraries over hand-rolling. If a well-maintained library solves the problem, use it.
- Evaluate libraries on: active maintenance, community size, license compatibility, security track record.
- Pin versions: stable libraries use `>=current,<next-major`; pre-1.0 libraries use `>=current,<next-minor`.
- Read changelogs before bumping any dependency. Never do blind `pixi update` across all deps.
- No circular dependencies. Packages (`m365_extract/`) never import from scripts (`scripts/`) or tests.

### Git Discipline
- Commit format: `<type>: <imperative summary>` (feat, fix, docs, chore, refactor, test)
- Never commit: secrets, generated envs, runtime output, machine-specific config
- Always commit: lock files, source code, config templates

### Definition of Done
- [ ] Implementation satisfies the stated requirement — no more, no less
- [ ] All existing tests pass
- [ ] New tests cover the new behavior (unit + integration where applicable)
- [ ] Property-based tests added for any new pure functions
- [ ] Linting passes (`pixi run lint`)
- [ ] Type-checking passes (ruff + mypy)
- [ ] No regressions in related modules
- [ ] No untested code paths introduced
- [ ] Dependencies pinned and lockfile updated
- [ ] No secrets, tokens, or PII in code, logs, or test fixtures
- [ ] Documentation updated if behavior or API changed
- [ ] CHANGELOG.md entry added if this is a user-visible change
- [ ] PR description explains *why* the change was made, not just what changed

## Agentic Engineering

Rules for Claude and any autonomous agent working in this repository.

### Core Rules

- **Understand before editing** -- Read the relevant source files and tests before making any change. Never edit code you haven't read.
- **Plan explicitly** -- For non-trivial changes, state what you will do, in what order, and how you will verify. Share the plan before executing.
- **Keep changes small** -- One logical concern per commit. Prefer a sequence of small, verifiable changes over a single large refactor.
- **Least privilege** -- Only touch files relevant to the task. Do not refactor, reformat, or "improve" adjacent code unless explicitly asked.
- **Stop on unexpected state** -- If you encounter failing tests, missing files, or behavior that contradicts your assumptions, stop and report before proceeding.
- **Preserve human trust** -- Changes must be reviewable. If a change cannot be explained clearly, it should not be made.
- **No hallucinations** -- Never invent API signatures, config keys, or library behaviors. Read the source or docs to confirm.

### Task Lifecycle

1. **Understand** -- Read the issue, linked files, and relevant source. Identify what is in scope and what is not.
2. **Clarify** -- If the requirement is ambiguous or has multiple valid approaches, ask before acting.
3. **Plan** -- State the steps, files to change, and verification method.
4. **Execute** -- Implement the minimum change that satisfies the requirement. Run tests.
5. **Verify** -- Confirm all tests pass, linting passes, and the Definition of Done is met.
6. **Report** -- Summarize what changed, why, and what was explicitly left out of scope.

### Change Safety

- **Minimal diff** -- Every line changed should be necessary. Remove unrelated edits before committing.
- **One concern per change** -- Do not bundle a bug fix with a refactor. Separate commits, separate PRs.
- **Stop before scope creep** -- If implementing the task requires changing more than 3 files, pause and verify the approach is correct.
- **No backwards compatibility by default** -- Refactor freely. Do not add migration shims, deprecated aliases, or dual code paths unless explicitly required.

### Escalation

Stop and request human confirmation before:
- Any destructive operation (file deletion, database mutation, irreversible action)
- Scope expansion beyond what was described in the issue
- Choosing between two approaches with meaningfully different trade-offs
- Any change that would break the public API or CLI interface

### Hard Boundaries

- Never force-push or rewrite published commits
- Never fabricate test data, expected outputs, or benchmark numbers
- Never edit files outside the repository (no cross-repo side effects)
- Never auto-send, auto-deploy, or auto-publish without explicit instruction

## Dark Factory Agent Context

### Label Taxonomy

| Label | Purpose |
|-------|---------|
| `type:bug` | Bug report |
| `type:feat` | Feature request |
| `type:chore` | Maintenance / improvement |
| `priority:1` | Urgent -- implement immediately |
| `priority:2` | Normal (default) |
| `priority:3` | Backlog |
| `claude:implement` | Trigger Claude to implement this issue |
| `status:triaged` | Issue has been triaged |
| `status:in-progress` | Claude is working on this |
| `status:pr-created` | PR created, awaiting review |
| `status:pr-draft` | Draft PR created, CI pending |
| `status:blocked` | Implementation blocked (see comments) |
| `autofix:1` | First auto-fix attempt |
| `autofix:2` | Second auto-fix attempt |
| `autofix:3` | Third (final) auto-fix attempt |
| `source:dep-audit` | Created by dependency audit agent |
| `source:security-scan` | Created by security scan agent |
| `source:code-quality` | Created by code quality agent |
| `source:test-coverage` | Created by test coverage agent |
| `source:docs-freshness` | Created by docs freshness agent |
| `source:workflow-upgrade` | Created by workflow upgrade agent |

### Build & Test

- Package manager: pixi
- Install: `pixi install`
- Test: `pixi run test-cov`
- Azurite tests: `pixi run test-azurite`
- Lint: `pixi run lint`
- Format: `pixi run format-check`
- Coverage threshold: 80%
- Test framework: pytest + hypothesis
- Python: >=3.12

### Testing Principles

- **No Hardcoded Test Data** -- Test fixtures derive expected values from the same logic under test, or use property-based tests. Never hardcode a number that was hand-computed.
- **Test Isolation** -- Each test is fully independent. No ordering dependencies, no shared mutable state between tests. `pytest-randomly` order must not break any test.
- **Verify, Do Not Trust** -- Assert the actual output, not a proxy. If a function writes a file, read the file and check its contents.
- **Verify by Change Type** -- New feature: add positive + negative tests. Bug fix: add a regression test that fails before the fix and passes after.
- **Property-Based Testing** -- Use `hypothesis` for any function with a clear input/output contract: parsers, transformers, formatters, validators. Pair with one concrete example test per property.

### Dependency Tooling

- Primary: pixi (conda-forge + PyPI)
- Lockfile: pixi.lock
- Audit: `pip audit` (run inside pixi shell)

### Security Standards

- Validate all external input (file paths, user-supplied strings) before use. Reject or sanitize rather than propagate.
- Sanitize outputs written to disk or passed to subprocesses. Never interpolate untrusted data into shell commands.
- Never log PII, tokens, secrets, or file contents unless explicitly building a debug mode.
- Use synthetic or anonymized data in tests. Never use real personal data in fixtures.
- Subprocess calls use list args (no `shell=True`). File paths validated and resolved before I/O operations.

### Documentation Standards

- README.md documents CLI usage and all config options
- All public functions have docstrings + type hints
- CHANGELOG.md updated for each release

### Code Quality Standards

- Max file length: 300 lines
- No sys.path manipulation
- Descriptive module names (no utils, core, lib)

### CI Setup

Steps needed before agent workflows can run tests:

1. Install pixi: `prefix-dev/setup-pixi@v0.8.8` with cache
2. For Azurite integration tests, CI uses an Azurite service container
3. No additional system dependencies required

### Pipeline Architecture

```
auth -> graph -> extract -> convert -> storage
```

## Admin Dashboard (Reflex)

The `m365_admin/` package is a Reflex SPA for managing sync settings, user preferences, and admin configuration.

### Architecture

- **Models**: `m365_extract/models.py` — plain SQLModel tables shared between Reflex and the sync daemon
- **Services**: `m365_admin/services/` — `TokenService` (Fernet encrypt/decrypt), `AdminService` (config CRUD, role check)
- **State classes**: `auth_state.py` (OAuth), `preferences_state.py` (extractor toggles), `admin_state.py` (user mgmt + config), `sync_state.py` (read-only sync history)
- **Pages**: `/login`, `/callback`, `/dashboard`, `/settings`, `/admin`
- **Components**: `components/sidebar.py` (nav), `components/layout.py` (page wrapper)
- **Database**: SQLModel with 5 tables (`user`, `tokenrecord`, `extractorpreference`, `adminconfig`, `syncrecord`). SQLite locally, PostgreSQL in production. Configured via `DATABASE_URL` env var.
- **Admin role**: Emails in `config.web.admin_emails` list. Checked via `AuthState.is_admin` computed var.
- **Write boundaries**: UI writes users/tokens/preferences/config. Daemon writes sync records. Neither overwrites the other's data.

### Deleted modules (Phase 5B cleanup)

- `m365_extract/web/` — replaced by Reflex admin UI
- `m365_extract/user_manager.py` — replaced by `User` SQLModel + `rx.session()`
- `m365_extract/auth/token_store.py` — replaced by `TokenRecord` SQLModel + `TokenService`
- CLI `serve` command — replaced by `pixi run -e admin dev`

### Gotchas

- **`on_load` fires multiple times per page load** — Reflex fires `on_load` handlers during both server-side render and client-side hydration. Any `on_load` handler must be idempotent. In `handle_callback()`, this means: (1) check `user_id` early-return if already authenticated, and (2) do NOT consume OAuth state tokens on verify — use TTL-based expiry instead.
- **Reflex state vars must be picklable** — Reflex serializes state between requests. Never store `sqlite3.Connection`, file handles, or other unpicklable objects as state class variables. Create fresh service instances per handler call.
- **External OAuth redirects lose Reflex state** — When the browser navigates to Entra (external redirect), the WebSocket disconnects and Reflex creates fresh state on return. Backend vars like `_oauth_state` are lost. Solution: persist OAuth state tokens to disk (`state/oauth_state.json`).
- **`router.page.params` is deprecated** — Use `self.router.url.query_parameters` (Reflex >=0.8.1). `router.url` is a `ReflexURL` (subclass of `str`); `query_parameters` is a frozen dict from `urllib.parse.parse_qsl`.
- **Config must have `web:` section** — `get_config()` validates this at load time. If `M365_ADMIN_CONFIG` points to the CLI config (no `web:` section), it crashes with a clear error. Set `M365_ADMIN_CONFIG=./config.web.yaml` in `.env`.
- **`Killing worker-0` warning during dev** — Granian (Reflex's ASGI server) killed the worker when `handle_callback()` blocked the event loop with synchronous MSAL + SQLite calls. Fixed: blocking calls are now wrapped in `asyncio.to_thread()`. If this warning reappears, check for new synchronous I/O added to event handlers.
- **State inheritance** — `PreferencesState`, `AdminState`, `SyncState` all inherit from `AuthState` so they share auth vars. Reflex merges substates; `on_load` handlers from parent and child both fire.
- **`DATABASE_URL` must be set** — `rxconfig.py` reads it at import time (before `config_loader`). Falls back to `sqlite:///state/web.db` for tests. Production must set the env var explicitly.
