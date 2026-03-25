# Changelog

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
