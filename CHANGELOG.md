# Changelog

## [0.3.0](https://github.com/neuralsignal/m365-extract/compare/v0.2.2...v0.3.0) (2026-04-13)


### Features

* add Alembic DB migrations - WP4 ([cb7793e](https://github.com/neuralsignal/m365-extract/commit/cb7793e7ffd454170758d23ac22536fffbf05d54))
* add config.deploy.yaml for Azure Blob storage in production ([74a7ea5](https://github.com/neuralsignal/m365-extract/commit/74a7ea5b4276eed2fa48f26341ba9df6186ea428))
* add email attachment download + dedup to m365-extract ([620b587](https://github.com/neuralsignal/m365-extract/commit/620b587fb00b7b7b43e823e9a3eff52c97d5d2c6))
* add observability (WP3) + type converters config (WP2e) ([5e01da6](https://github.com/neuralsignal/m365-extract/commit/5e01da699a0de4a990d53456a1e82720d94323bf))
* add sync type and pagination transparency logging for email ([a09cda0](https://github.com/neuralsignal/m365-extract/commit/a09cda0cbc032909a02d8e29eed3ad70d3df66aa))
* add truncation indicator to teams chat frontmatter ([4460228](https://github.com/neuralsignal/m365-extract/commit/4460228b92e26e57c564e96ec2442dc1dde6d647))
* enable onedrive, sharepoint, contacts extractors ([55bf9c6](https://github.com/neuralsignal/m365-extract/commit/55bf9c6db4ee8df6b2165b15421522947600fa50))
* harden continuous mode with auth failure recovery and heartbeat logging ([f5d8ea6](https://github.com/neuralsignal/m365-extract/commit/f5d8ea6f63d3f8ac5c5faee13a70f93ea0ff4575))
* implement contacts and directory extractors ([79c9149](https://github.com/neuralsignal/m365-extract/commit/79c914953e9beb23999a7b927b0a0011fbe63508))
* implement contacts and directory extractors ([4666ef0](https://github.com/neuralsignal/m365-extract/commit/4666ef020b57798dab05fa4c21a85f9f11eef138)), closes [#16](https://github.com/neuralsignal/m365-extract/issues/16)
* include attendee email and status in calendar events ([0c35526](https://github.com/neuralsignal/m365-extract/commit/0c355264731f3e9a87f7082f19ea5596e35be5ec))
* merge daemon into web app (WP8) + constitution fixes ([b06f67c](https://github.com/neuralsignal/m365-extract/commit/b06f67c863dd72a5f956c523e45894e0cf54fc55))
* per-user storage isolation in daemon ([8a16324](https://github.com/neuralsignal/m365-extract/commit/8a16324de4d72212682abc778161e17a5eaff9d9))
* Sub-phase 4A — TokenStore, UserManager, WebConfig ([9bf5c3c](https://github.com/neuralsignal/m365-extract/commit/9bf5c3cb097ea6648ea9b8354a6bc905a75f5ace))
* Sub-phase 4B — auth code flow + web token provider ([c649172](https://github.com/neuralsignal/m365-extract/commit/c6491725a15802632dfbe1e30ee11f4b257ab49d))
* Sub-phase 4C — FastAPI app shell with auth, sync, health endpoints ([da325e8](https://github.com/neuralsignal/m365-extract/commit/da325e82c815ef4bb5789ef33b217d4354b82dbf))
* Sub-phase 4D — scheduler + admin routes ([758bec4](https://github.com/neuralsignal/m365-extract/commit/758bec4c6a7fdad48a2d1206edbdac5a6cef0720))
* unify deployment config — single config.deploy.yaml for both services ([d7b848d](https://github.com/neuralsignal/m365-extract/commit/d7b848d96db4b58ac3b0473f1589fd6ea3743d0d))


### Bug Fixes

* add missing email config fields and fix token expiry buffer in tests ([5874b96](https://github.com/neuralsignal/m365-extract/commit/5874b9666ac91288dcae0d156f0b0f378671fab1))
* add missing email config fields to test fixtures and fix token expiry buffer ([1588b7f](https://github.com/neuralsignal/m365-extract/commit/1588b7ff3cb3fc26bdac09770f6201b6a878b2e6))
* add missing graph config fields to admin config loader test fixtures ([e02a30a](https://github.com/neuralsignal/m365-extract/commit/e02a30a711a3322c643bc032e3e26796e7610413))
* add missing graph config fields to admin config loader test fixtures ([5d515d7](https://github.com/neuralsignal/m365-extract/commit/5d515d7fdf82d9d072f9497d3433c70ee5036d33))
* add missing graph config fields to admin config loader tests ([e525471](https://github.com/neuralsignal/m365-extract/commit/e525471e48bbee88a7e3de2ad6b213849affbe59))
* add missing graph config fields to admin test fixtures ([903b35b](https://github.com/neuralsignal/m365-extract/commit/903b35baec05bc35f2baa971334ff6a2088719f7))
* add missing graph config fields to admin test fixtures ([b283168](https://github.com/neuralsignal/m365-extract/commit/b28316849619e902072d06a9e33a3f8d256bc94f))
* add missing graph config fields to admin test fixtures ([6b3478f](https://github.com/neuralsignal/m365-extract/commit/6b3478ffc9aa85c5ef0328df9fe5f39ae5963d2c))
* add missing graph config fields to admin test fixtures ([832b5c5](https://github.com/neuralsignal/m365-extract/commit/832b5c5d866ce0d5d90328901f19369d6c3ef3fc))
* align test_returns_cached_token_when_valid expires_at with token buffer ([#98](https://github.com/neuralsignal/m365-extract/issues/98)) ([63a887b](https://github.com/neuralsignal/m365-extract/commit/63a887b473e92c4aba883ee32d2207febae244b4))
* auto-fix CI failures (attempt 1) ([3771c6d](https://github.com/neuralsignal/m365-extract/commit/3771c6d4c0c179f7825981ed9b5883e2947c06f9))
* auto-fix CI failures (attempt 1) ([fd878e5](https://github.com/neuralsignal/m365-extract/commit/fd878e5681f9a9c700f8faf434681f0ca7b24282))
* auto-fix CI failures (attempt 1) ([ea1ae0b](https://github.com/neuralsignal/m365-extract/commit/ea1ae0b4af103b4f400d0552ad2388990f0a2e65))
* auto-fix CI failures (attempt 1) ([9a049d4](https://github.com/neuralsignal/m365-extract/commit/9a049d4c7a3fbb8abc5390812c1d1858db3dfbfc))
* auto-fix CI failures (attempt 1) ([7c6d081](https://github.com/neuralsignal/m365-extract/commit/7c6d081bc07e2f2816256ed803420820d586b39c))
* auto-fix CI failures (attempt 1) ([bd4ef3a](https://github.com/neuralsignal/m365-extract/commit/bd4ef3ad4868fe7753bc40af091b36a0f778fb79))
* auto-fix CI failures (attempt 1) ([08726ff](https://github.com/neuralsignal/m365-extract/commit/08726ff33c0109ffd7d9bc98c7f0d56b2b784482))
* auto-fix CI failures (attempt 1) ([18a492c](https://github.com/neuralsignal/m365-extract/commit/18a492c249aef788addba6e2331b68547c8dabd3))
* auto-fix CI failures (attempt 1) ([72c16bd](https://github.com/neuralsignal/m365-extract/commit/72c16bd99633acff8a2a79aa6d191770ec1afe83))
* create writable state dir for daemon in Docker ([043270a](https://github.com/neuralsignal/m365-extract/commit/043270a0744e2ad775e30498601bed344b930778))
* enable WebSockets + upgrade Actions to Node.js 24 ([773374f](https://github.com/neuralsignal/m365-extract/commit/773374fe260ade63c94343de6ab7bae95c0b3f98))
* Graph API compatibility for teams_channels, contacts, and null sender ([e6d4b21](https://github.com/neuralsignal/m365-extract/commit/e6d4b219898172ef742abf441811b1f27fa84be5))
* narrow broad except Exception to specific types in email extractor ([#94](https://github.com/neuralsignal/m365-extract/issues/94)) ([9156710](https://github.com/neuralsignal/m365-extract/commit/9156710c04c31d45e026b4d7b3be91b2c7fa1e11))
* pin pypdf &gt;=6.9.2 to mitigate CVE-2026-33699 infinite loop DoS ([#91](https://github.com/neuralsignal/m365-extract/issues/91)) ([5cc359d](https://github.com/neuralsignal/m365-extract/commit/5cc359d3ae5f03156f6084c57f5db09b6a4bf69b))
* populate items_synced in SyncRecord from extractor counts ([181e813](https://github.com/neuralsignal/m365-extract/commit/181e8132976a36ba88d9e5a813ac528c45b7af20))
* remove magic number and duplicated config fallback in worker.py ([#95](https://github.com/neuralsignal/m365-extract/issues/95)) ([cfd4896](https://github.com/neuralsignal/m365-extract/commit/cfd4896efe547f25bf2b67aa74c9abe068130137))
* serialize ConvertersConfig to dict before passing to extractors ([#44](https://github.com/neuralsignal/m365-extract/issues/44)) ([5b9c1e0](https://github.com/neuralsignal/m365-extract/commit/5b9c1e02ff0fef356055161823045c1c2ed7e249))
* use JSON params files instead of .bicepparam for Bicep deploy ([9c7c567](https://github.com/neuralsignal/m365-extract/commit/9c7c567c74a516feb312f745e7513df578afa600))
* validate and cap Retry-After header in graph client ([#70](https://github.com/neuralsignal/m365-extract/issues/70)) ([81311e6](https://github.com/neuralsignal/m365-extract/commit/81311e635cdf3355794140f805732ea0739f8b62))
* validate download URL domains and sanitize SAS tokens from logs ([#41](https://github.com/neuralsignal/m365-extract/issues/41)) ([c5cb8b5](https://github.com/neuralsignal/m365-extract/commit/c5cb8b58581480d10ee25d5a13d0d5d944f14fbd))
* web app must use config.web.yaml (no blob storage env vars) ([264358c](https://github.com/neuralsignal/m365-extract/commit/264358cabcf0eea572549c1417a881d90bcbf7f2))


### Documentation

* update MATURITY.md and roadmap.md to reflect dark factory progress ([5bd1bab](https://github.com/neuralsignal/m365-extract/commit/5bd1bab80ce629af810da555e332df8cd205f034))

## [Unreleased]

### Security

* Pin `pyopenssl>=26.0,<27` to fix CVE-2026-27448 (TLS callback bypass) and CVE-2026-27459 (buffer overflow)
* Upgrade `pypdf` from `6.9.2` to `6.10.0` to fix CVE-2026-40260 (memory DoS via crafted PDF XMP metadata, GHSA-3crg-w4f6-42mx)

### Features

* **worker refactor**: replaced monolithic daemon thread with independent sync worker (`m365_extract/worker.py`)
  - Per-(user, extractor) jobs via `ThreadPoolExecutor` with configurable concurrency
  - PostgreSQL advisory locks prevent duplicate job runs
  - `ExtractorStatus` model replaces `SyncRecord` (single row per user+extractor pair)
  - New CLI command `m365-extract worker` for standalone multi-user sync
  - `start_worker_thread()` bridge for single-container deployment
  - Per-extractor state files eliminate concurrent write races
  - Dashboard shows per-extractor status grid
  - `WorkerConfig` section added to config schema
  - docker-compose updated with separate `worker` service
  - Alembic migration: `syncrecord` → `extractorstatus`

### Breaking Changes

* CLI `sync --continuous` removed (replaced by `worker` command)
* `SyncRecord` model replaced by `ExtractorStatus`
* `daemon.py`, `daemon_runner.py`, `continuous.py` deleted

## [0.2.2](https://github.com/neuralsignal/m365-extract/compare/v0.2.1...v0.2.2) (2026-03-20)


### Bug Fixes

* log warning instead of silently swallowing exceptions in teams_chats.py ([#15](https://github.com/neuralsignal/m365-extract/issues/15)) ([2792534](https://github.com/neuralsignal/m365-extract/commit/279253433bdd725ecc811dad75dc0b6804bb66b4))
* replace bare RuntimeError with GraphApiError in graph_client.py ([#17](https://github.com/neuralsignal/m365-extract/issues/17)) ([f64cb90](https://github.com/neuralsignal/m365-extract/commit/f64cb901491b4a8f096dc6e5a840355744bf6448))
* resolve merge conflicts with main ([deb79e8](https://github.com/neuralsignal/m365-extract/commit/deb79e82b41ee1185b8ffd72ec0adc9a1c350d53))

## [0.2.1](https://github.com/neuralsignal/m365-extract/compare/v0.2.0...v0.2.1) (2026-03-18)


### Bug Fixes

* add path traversal protection to LocalBackend ([3e8a1f3](https://github.com/neuralsignal/m365-extract/commit/3e8a1f3e5b411c0f38dc1a50883cf1c247e14d26))
* add path traversal protection to LocalBackend ([d670501](https://github.com/neuralsignal/m365-extract/commit/d670501165e05ee09bb8368630c568eec199e61c)), closes [#3](https://github.com/neuralsignal/m365-extract/issues/3)
* auto-fix CI failures (attempt 1) ([eae79af](https://github.com/neuralsignal/m365-extract/commit/eae79af2b87ce3132df17790dcceecaeb697dcd8))
* format local.py to pass ruff format check ([68102b5](https://github.com/neuralsignal/m365-extract/commit/68102b556c6773f17d388a2ae2097b7375652caf))
* set restrictive permissions (0600) on MSAL token cache file ([ae73ff0](https://github.com/neuralsignal/m365-extract/commit/ae73ff0e535e2eef775f1a1431f0c40894eb6248))
* set restrictive permissions (0600) on MSAL token cache file ([a41314a](https://github.com/neuralsignal/m365-extract/commit/a41314ab018c9418ac357d45a095f2d89e0df115)), closes [#4](https://github.com/neuralsignal/m365-extract/issues/4)

## [0.2.0](https://github.com/neuralsignal/m365-extract/compare/v0.1.0...v0.2.0) (2026-03-17)


### Features

* initial release with full packaging infrastructure ([7ab5361](https://github.com/neuralsignal/m365-extract/commit/7ab5361201bbdfaa51d2b6333c4fc66de815be25))


### Bug Fixes

* disable --locked for setup-pixi in release lockfile update job ([4b8a5cc](https://github.com/neuralsignal/m365-extract/commit/4b8a5cccaab8d66f1ea28a0a749c9bfcc7f2066c))
* use docker run with --skipApiVersionCheck for Azurite in CI ([470b82a](https://github.com/neuralsignal/m365-extract/commit/470b82ae980b632a73f8929e50dd74fce9856cd8))

## [0.1.0] - 2026-03-17

### Added
- Core Graph API client with delta sync, pagination, retry, rate limiting
- MSAL device code authentication with token caching
- 6 extractors: email, calendar, teams_chats, teams_channels, onedrive, sharepoint
- Local filesystem storage backend
- Azure Blob Storage backend with Azurite dev workflow
- Document conversion via obsidian-import integration
- CLI: `m365-extract auth login`, `sync --once`, `sync --continuous`
- Frozen dataclass config with strict validation and env var expansion
- Bicep IaC templates for Azure Storage (dev/prod)
- Docker images (Dockerfile.web, Dockerfile.daemon — multi-stage, non-root)
- Docker Compose with Azurite emulator (profile)
- 158 unit tests + 9 Azurite integration tests
