# Changelog

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
