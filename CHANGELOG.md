# Changelog

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
- Dockerfile (multi-stage, non-root)
- Docker Compose with Azurite emulator
- 158 unit tests + 9 Azurite integration tests
