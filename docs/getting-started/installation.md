# Installation

## Requirements

- Python >= 3.12
- An Azure AD (Entra ID) app registration with delegated Graph API permissions

## From PyPI

Install the base package:

```bash
pip install m365-extract
```

### Extras

| Extra | What it adds | When you need it |
|-------|-------------|-----------------|
| `azure` | `azure-storage-blob` | Using Azure Blob Storage backend |
| `convert` | `obsidian-import[markitdown,docling]` | Converting Office documents (DOCX, PPTX, XLSX, PDF) to markdown |
| `web` | `fastapi`, `uvicorn`, `apscheduler`, `cryptography` | Running as a web service (future) |
| `all` | All of the above | Full installation |

```bash
# Azure Blob Storage support
pip install "m365-extract[azure]"

# Document conversion
pip install "m365-extract[convert]"

# Everything
pip install "m365-extract[all]"
```

## Development Setup

The project uses [pixi](https://pixi.sh/) for development dependency management.

```bash
# Clone the repository
git clone https://github.com/neuralsignal/m365-extract.git
cd m365-extract

# Install all dependencies (including dev tools)
pixi install

# Install pre-commit hooks
pixi run pre-commit-install

# Run tests
pixi run test

# Run linter
pixi run lint

# Format code
pixi run format
```

### Available pixi tasks

| Task | Command | Description |
|------|---------|-------------|
| `test` | `pixi run test` | Run unit tests (excludes integration and Azurite tests) |
| `test-cov` | `pixi run test-cov` | Run tests with coverage report |
| `test-azurite` | `pixi run test-azurite` | Run Azurite integration tests (requires running Azurite) |
| `lint` | `pixi run lint` | Run ruff linter |
| `format` | `pixi run format` | Format code with ruff |
| `format-check` | `pixi run format-check` | Check formatting without modifying files |
| `docs-build` | `pixi run docs-build` | Build MkDocs documentation site |
| `docs-serve` | `pixi run docs-serve` | Serve documentation locally with live reload |
