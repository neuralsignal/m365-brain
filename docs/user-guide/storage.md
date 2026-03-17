# Storage Backends

m365-extract supports two storage backends: local filesystem and Azure Blob Storage. Both implement the `StorageBackend` protocol and are interchangeable -- switching backends requires only a config change.

## StorageBackend Protocol

All backends implement this interface:

```python
class StorageBackend(Protocol):
    def write_file(self, path: str, content: str) -> None: ...
    def read_file(self, path: str) -> str: ...
    def file_exists(self, path: str) -> bool: ...
    def list_files(self, prefix: str) -> list[str]: ...
    def delete_file(self, path: str) -> None: ...
```

All paths are relative (e.g., `emails/2026/2026-03-15/subject-a1b2c3/index.md`). The backend handles mapping these to absolute filesystem paths or blob names.

## Local Backend

Stores files on the local filesystem under a base directory. Ideal for personal use with Obsidian, where the vault directory is the base path.

### Configuration

```yaml
storage:
  backend: "local"
  local:
    base_path: "./vault"
```

| Key | Type | Description |
|-----|------|-------------|
| `base_path` | `str` | Root directory for all stored files. Relative paths resolved against config directory. Created automatically if it does not exist. |

### Behavior

- **Write:** Creates parent directories automatically. Overwrites existing files.
- **Read:** Returns UTF-8 content. Raises `FileNotFoundError` if missing.
- **Delete:** Removes the file and cleans up empty parent directories up to the base path.
- **List:** Recursively lists all files under a prefix, returning paths relative to the base directory.

### Directory Structure

```
vault/
  emails/
    2026/
      2026-03-15/
        weekly-standup-a1b2c3/
          index.md
  calendar/
    2026/
      2026-03/
        2026-03-15-standup-d4e5f6.md
  teams-chats/
    project-alpha_g7h8i9.md
  teams-channels/
    engineering/
      general-j0k1l2.md
  onedrive/
    Documents/
      report.docx.md
  sharepoint/
    intranet/
      shared-docs/
        handbook.docx.md
```

## Azure Blob Storage Backend

Stores files as blobs in an Azure Storage container. Supports per-user or per-tenant isolation via a configurable prefix.

### Configuration

```yaml
storage:
  backend: "azure_blob"
  azure_blob:
    connection_string: "${AZURE_STORAGE_CONNECTION_STRING}"
    container_name: "${AZURE_STORAGE_CONTAINER}"
    prefix: "${AZURE_STORAGE_PREFIX}"
```

| Key | Type | Description |
|-----|------|-------------|
| `connection_string` | `str` | Azure Storage connection string. Use env var expansion for secrets. |
| `container_name` | `str` | Blob container name. Created automatically if it does not exist. |
| `prefix` | `str` | Path prefix prepended to all blob names (e.g., `"user1/"` or `"tenant-abc/"`). Enables isolation within a shared container. |

### Behavior

- **Write:** Uploads UTF-8 content as a blob, overwriting if it exists.
- **Read:** Downloads and decodes blob content as UTF-8.
- **Delete:** Deletes the blob. No error if it does not exist.
- **List:** Lists blobs under a prefix, stripping the internal prefix from returned paths.

### Blob Naming

With `prefix: "user1/"`, a file path like `emails/2026/subject.md` becomes blob name `user1/emails/2026/subject.md`.

### Authentication

The Azure Blob backend uses a connection string for authentication. For production deployments, use Azure Key Vault or managed identity to avoid storing connection strings in config files.

!!! tip "Azurite for local development"
    Use the [Azurite](https://learn.microsoft.com/en-us/azure/storage/common/storage-use-azurite) emulator for local development and testing. See the [Azure Setup](azure.md) guide for the Docker Compose configuration.
