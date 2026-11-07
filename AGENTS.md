<!-- TRELLIS:START -->
# Trellis Instructions

These instructions are for AI assistants working in this project.

This project is managed by Trellis. The working knowledge you need lives under `.trellis/`:

- `.trellis/workflow.md` — development phases, when to create tasks, skill routing
- `.trellis/spec/` — package- and layer-scoped coding guidelines (read before writing code in a given layer)
- `.trellis/workspace/` — per-developer journals and session traces
- `.trellis/tasks/` — active and archived tasks (PRDs, research, jsonl context)

If a Trellis command is available on your platform (e.g. `/trellis:finish-work`, `/trellis:continue`), prefer it over manual steps. Not every platform exposes every command.

If you're using Codex or another agent-capable tool, additional project-scoped helpers may live in:
- `.agents/skills/` — reusable Trellis skills
- `.codex/agents/` — optional custom subagents

Managed by Trellis. Edits outside this block are preserved; edits inside may be overwritten by a future `trellis update`.

<!-- TRELLIS:END -->

# m365-brain

Microsoft 365 data extraction to Obsidian-compatible markdown via the Graph API, plus the
knowledge index over it and a Reflex admin dashboard. Goals: `INTENT.md`; interfaces:
`CONTRACTS.md`; CLI and config reference: `README.md`.

## Commands

| Task | Command |
|------|---------|
| Install | `pixi install` |
| Unit tests + coverage (threshold 80%) | `pixi run test-cov` |
| Azurite storage tests | `pixi run test-azurite` |
| Admin dashboard tests | `pixi run -e admin test-admin` |
| Admin dashboard dev server | `pixi run -e admin dev` |
| Lint / format check | `pixi run lint`, `pixi run format-check` |
| Dependency audit | `pip audit` inside the pixi shell (lockfile: `pixi.lock`) |

Python >= 3.12. pytest + hypothesis. CI installs pixi with `prefix-dev/setup-pixi` and runs
Azurite as a service container; no other system dependencies.

## Repo map

| Path | Purpose |
|------|---------|
| `m365_brain/cli.py` | Click CLI. Top-level: `init`, `run`, `extract`, `status`. Groups: `auth`, `config`, `index`, `outbox`, `files`, `teams`, `vault`, `ops`. **There is no `sync` verb and no `worker` verb.** |
| `m365_brain/commands/` | One module per command group; `_context.py` holds shared wiring, exit codes and `emit` |
| `m365_brain/index/` | Knowledge half: SQLite/FTS5 + in-memory backends, search, vectors, file catalog |
| `m365_brain/m365/` | Microsoft half: Graph client, auth profiles, extractors, converters, outbox handlers |
| `m365_brain/vault/` | Every path in the vault, plus the intent envelope and payload schemas |
| `m365_brain/outbox/` | Vendor-agnostic write-back: authorities, registry, runner, reconcile |
| `m365_brain/worker.py` | Multi-user per-(user, extractor) job loop. A library module, not a CLI verb: the admin app runs it as a thread |
| `m365_admin/` | Reflex admin dashboard (OAuth, preferences, admin, sync status) |
| `infra/` | Bicep IaC |

## Logging

structlog is the only logging mechanism. No `click.echo`, `print`, or stdlib `logging` for operational output.

- **Central config**: `m365_brain/logging_config.py` — call `configure_logging(log_level, json_output)` once at startup
- **Entry points**: the root group calls `route_logs_to_stderr()` before dispatch, and `require_config()` calls `configure_logging()` at the one funnel every config-taking verb passes through. It used to be called by `run` and `extract` alone, so every other verb ran on structlog's stdout-writing default and `outbox list --json` emitted 54 log lines ahead of its JSON
- **Event naming**: `module.action` (e.g., `email.sync_complete`, `graph.retryable_error`, `cli.dry_run_auth_ok`)
- **Renderer**: JSON when `service.json_logs: true` (daemon/production), ConsoleRenderer when false (dev/interactive)
- **Never construct a bare `ConsoleRenderer()`.** Without an explicit `exception_formatter` structlog picks one from a fallback chain — rich → `better_exceptions` → plain — and the first two print **every frame's local variables**. A downstream deployment whose env happened to carry rich wrote MSAL cookies, the tenant and user object IDs, and the `grant_type=refresh_token` body into a 38 MB log on disk. `logging_config.py` pins `structlog.dev.plain_traceback`; leave it pinned. `RichTracebackFormatter(show_locals=False)` is not a substitute: rich is not a dependency here, so its stub raises `ModuleNotFoundError`, and `better_exceptions` is still next in the chain
- **Log levels**: debug (pagination, internal state), info (sync lifecycle), warning (skipped items, truncation), error (failures that continue), critical (daemon exit)
- **Exception**: `click.echo` is acceptable in `auth login` and `auth status` commands (user-facing interactive output, not operational logging)

## Engineering standards

Non-negotiable. Violating these is a bug.

- KISS, DRY, YAGNI. No capability, config key or code path without a current use.
- No default arguments in function signatures. Every value comes from config or the caller.
- Fail fast and loud: no try-except-pass. Every catch block logs, re-raises or corrects.
  Error messages say what failed, with what input, and what the caller should do.
- Custom exception classes with context (`GraphApiError`, `ConfigError`, `ExtractorError`), never
  bare `Exception`.
- Type hints on every argument and return value.
- No `sys.path` manipulation; no package named `src`, `lib`, `utils` or `core`.
- Thin CLI: business logic lives in library modules. The CLI layer calls the library layer, which
  calls I/O; no layer skipping. One extractor never imports from another. Packages never import
  from `scripts/` or `tests/`.
- Idempotent scripts and operations: interrupted and restarted = one clean run.
- Composition over inheritance. Comments explain why, not what.
- Pin dependencies: stable `>=current,<next-major`, pre-1.0 `>=current,<next-minor`. Read the
  changelog before a bump; never a blanket `pixi update`.
- Max file length 300 lines.

## Testing

- Write the failing test first. New feature: positive and negative tests. Bug fix: a regression
  test that fails before the fix.
- hypothesis for pure functions (parsers, transformers, formatters, validators), plus one concrete
  example per property.
- Never hardcode a hand-computed expected value. Assert the real output (read the written file).
- Tests are independent; `pytest-randomly` order must not break any.
- Synthetic data only. Never real personal data in fixtures.

## Documentation

- `README.md` documents CLI usage and every config option. Update it when behaviour or the CLI
  changes.
- Every public function has a docstring and type hints.

## Security

- Validate external input (paths, user strings) before use. Resolve paths before I/O.
- Subprocess calls use list args, never `shell=True`.
- Never log PII, tokens, secrets or file contents.

## Git and release

- Commit subject: `<type>: <imperative summary>` (feat, fix, docs, chore, refactor, test). It
  becomes the release note: `CHANGELOG.md` is generated by release-please and **never
  hand-edited**; a manual section is overwritten on the next release PR.
- Commit lock files. Never commit secrets, generated envs, runtime output or machine config.
- PR descriptions explain why.
- Never force-push or rewrite published commits. Never auto-deploy or auto-publish without an
  explicit instruction. Ask before destructive operations and before breaking the CLI or public API.

## Change safety

- No backwards compatibility by default: no migration shims, deprecated aliases or dual code
  paths unless the issue asks for them.
- Minimal diff, one concern per change: a bug fix and a refactor go in separate PRs.
- Stop and report on unexpected state (failing tests, missing files, behaviour that contradicts
  the issue) and before scope grows beyond the issue or past ~3 files.
- Never edit files outside this repository.

## Dark Factory agent context

GitHub workflows (`.github/workflows/`) run agents that read this file for labels and standards.

### Label taxonomy

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

## MSAL and Graph transports

Graph goes out over **httpx**; MSAL reaches `login.microsoftonline.com` over **requests**, its own dependency. One network fault therefore raises two unrelated types depending only on which call was in flight, and `GraphClient`'s retry loop speaks httpx. `m365/auth/msal_http.py` is the only module that imports `requests` — it translates to `AuthTransportError` at the edge and supplies the timeout MSAL otherwise omits (`requests` with no `timeout` waits forever). Wrap any new MSAL call in `auth_transport_errors()`, including ones that look like pure cache reads: `get_accounts()` and `PublicClientApplication.__init__` both fall through to instance discovery over the network.

## Admin dashboard (Reflex)

- Models: `m365_brain/models.py`, SQLModel tables shared by the dashboard and the worker. SQLite
  locally, PostgreSQL in production, via `DATABASE_URL`.
- Write boundaries: the UI writes users, tokens, preferences and config; the worker writes
  extractor status. Neither overwrites the other's data. They communicate only through the
  database.
- Admins are the emails in `config.web.admin_emails` (`AuthState.is_admin`).

### Gotchas

- **`on_load` fires multiple times per page load** — Reflex fires `on_load` handlers during both server-side render and client-side hydration. Any `on_load` handler must be idempotent. In `handle_callback()`, this means: check `user_id` early-return if already authenticated. OAuth state tokens ARE consumed on first successful verification (one-time use per RFC 6749 §10.12) — this is safe because Reflex serializes state events per-session, so the second `on_load` early-returns on `user_id` before reaching CSRF check.
- **Reflex state vars must be picklable** — Reflex serializes state between requests. Never store `sqlite3.Connection`, file handles, or other unpicklable objects as state class variables. Create fresh service instances per handler call.
- **External OAuth redirects lose Reflex state** — When the browser navigates to Entra (external redirect), the WebSocket disconnects and Reflex creates fresh state on return. Backend vars like `_oauth_state` are lost. Solution: persist OAuth state tokens to disk (`state/oauth_state.json`).
- **`router.page.params` is deprecated** — Use `self.router.url.query_parameters` (Reflex >=0.8.1). `router.url` is a `ReflexURL` (subclass of `str`); `query_parameters` is a frozen dict from `urllib.parse.parse_qsl`.
- **Config must have `web:` section** — `get_config()` validates this at load time. If `M365_ADMIN_CONFIG` points to the CLI config (no `web:` section), it crashes with a clear error. Set `M365_ADMIN_CONFIG=./config.web.yaml` in `.env`.
- **`Killing worker-0` warning during dev** — Granian (Reflex's ASGI server) killed the worker when `handle_callback()` blocked the event loop with synchronous MSAL + SQLite calls. Fixed: blocking calls are now wrapped in `asyncio.to_thread()`. If this warning reappears, check for new synchronous I/O added to event handlers.
- **State inheritance** — `PreferencesState`, `AdminState`, `SyncState` all inherit from `AuthState` so they share auth vars. Reflex merges substates; `on_load` handlers from parent and child both fire.
- **`DATABASE_URL` must be set** — `rxconfig.py` reads it at import time (before `config_loader`). Falls back to `sqlite:///state/web.db` for tests. Production must set the env var explicitly.

## Memory

Consolidated repo memory lives in `.agents/memory/`, written only by the memory dreamer.
Record new durable facts in your inbox (Claude: auto-memory; other harnesses:
`~/.agents/inbox/m365-brain/`), never in `.agents/memory/`. Index:

@.agents/memory/MEMORY.md

## TL;DR section

End every reply with a `## TL;DR` section: one or two lines carrying the decision — the
verdict, the recommendation, or what the reader now has to decide. Not a recap of the work.
