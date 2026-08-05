# Configuration

All behavior is controlled by a single `config.yaml` file. No defaults exist in code -- every value must be present in config or the application crashes with a clear error message.

## Environment Variable Expansion

String values support `${VAR_NAME}` syntax. The variable must be set in the environment (or in a `.env` file); missing variables cause a startup crash.

```yaml
auth:
  client_id: "${MSAL_CLIENT_ID}"
  tenant_id: "${MSAL_TENANT_ID}"
```

## Path Resolution

Path values (`base_path`, `state_file_path`, `token_cache_path`) that are relative are resolved against the config file's directory, not the process working directory.

```yaml
# If config.yaml is at /home/user/m365-brain/config.yaml
# then this resolves to /home/user/m365-brain/vault/
storage:
  local:
    base_path: "./vault"
```

## Full Reference

### `auth`

Authentication settings for MSAL device code flow.

| Key | Type | Description |
|-----|------|-------------|
| `client_id` | `str` | Azure AD application (client) ID. Use `${MSAL_CLIENT_ID}`. |
| `tenant_id` | `str` | Azure AD directory (tenant) ID. Use `${MSAL_TENANT_ID}`. |
| `scopes` | `list[str]` | Microsoft Graph API permission scopes to request. |
| `token_cache_path` | `str` | Path to the MSAL token cache file. Relative paths resolved against config directory. |

```yaml
auth:
  client_id: "${MSAL_CLIENT_ID}"
  tenant_id: "${MSAL_TENANT_ID}"
  scopes:
    - "User.Read"
    - "Mail.Read"
    - "Calendars.Read"
    - "Chat.Read"
    - "ChannelMessage.Read.All"
    - "Files.Read.All"
    - "Sites.Read.All"
    - "offline_access"
  token_cache_path: "./state/token_cache.json"
```

### `service`

Service-level settings.

| Key | Type | Description |
|-----|------|-------------|
| `mode` | `str` | Execution mode. Currently only `"cli"` is supported. |
| `log_level` | `str` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |

```yaml
service:
  mode: "cli"
  log_level: "INFO"
```

### `storage`

Storage backend selection and configuration.

| Key | Type | Description |
|-----|------|-------------|
| `backend` | `str` | Backend to use: `"local"` or `"azure_blob"`. |
| `local` | `LocalStorageConfig` | Local filesystem config. Required when `backend` is `"local"`. |
| `azure_blob` | `AzureBlobStorageConfig` | Azure Blob config. Required when `backend` is `"azure_blob"`. |

#### `storage.local`

| Key | Type | Description |
|-----|------|-------------|
| `base_path` | `str` | Root directory for stored files. Relative paths resolved against config directory. |

#### `storage.azure_blob`

| Key | Type | Description |
|-----|------|-------------|
| `connection_string` | `str` | Azure Storage connection string. Use `${AZURE_STORAGE_CONNECTION_STRING}`. |
| `container_name` | `str` | Blob container name. Use `${AZURE_STORAGE_CONTAINER}`. |
| `prefix` | `str` | Path prefix within the container (e.g., `"user1/"` for per-user isolation). |

=== "Local storage"

    ```yaml
    storage:
      backend: "local"
      local:
        base_path: "./vault"
    ```

=== "Azure Blob Storage"

    ```yaml
    storage:
      backend: "azure_blob"
      azure_blob:
        connection_string: "${AZURE_STORAGE_CONNECTION_STRING}"
        container_name: "${AZURE_STORAGE_CONTAINER}"
        prefix: "${AZURE_STORAGE_PREFIX}"
    ```

### `graph`

Microsoft Graph API client settings.

| Key | Type | Description |
|-----|------|-------------|
| `max_retries` | `int` | Maximum retry attempts for failed requests (handles 429, 500, 502, 503, 504). |
| `backoff_base_ms` | `int` | Base backoff duration in milliseconds. Doubles on each retry attempt. |
| `timeout_seconds` | `int` | HTTP request timeout in seconds. |
| `max_pages` | `int` | Maximum number of pages to follow when paginating results. Safety limit. |

```yaml
graph:
  max_retries: 3
  backoff_base_ms: 2000
  timeout_seconds: 30
  max_pages: 100
```

### `state`

Sync state persistence.

| Key | Type | Description |
|-----|------|-------------|
| `state_file_path` | `str` | Path to the JSON file storing delta links and sync timestamps. Relative paths resolved against config directory. |

```yaml
state:
  state_file_path: "./state/sync_state.json"
```

### `extractors`

Per-extractor configuration. Each extractor has an `enabled` flag and extractor-specific settings.

#### `extractors.email`

| Key | Type | Description |
|-----|------|-------------|
| `enabled` | `bool` | Enable or disable the email extractor. |
| `poll_interval_minutes` | `int` | Interval between syncs in worker mode. |
| `folders` | `list[str]` | Mail folders to sync. Valid values: `Inbox`, `SentItems`, `Drafts`, `Archive`, `DeletedItems`, `JunkEmail`. |
| `lookback_days` | `int` | Number of days to look back on first sync (before any delta link exists). |
| `max_items_per_sync` | `int` | Maximum number of emails to write per sync cycle per folder. |

```yaml
extractors:
  email:
    enabled: true
    poll_interval_minutes: 3
    folders: ["Inbox", "SentItems", "Archive"]
    lookback_days: 365
    max_items_per_sync: 500
```

#### `extractors.calendar`

| Key | Type | Description |
|-----|------|-------------|
| `enabled` | `bool` | Enable or disable the calendar extractor. |
| `poll_interval_minutes` | `int` | Interval between syncs in worker mode. |
| `lookback_days` | `int` | Number of days to look back for past events. Future events are always fetched up to 90 days ahead. |

```yaml
extractors:
  calendar:
    enabled: true
    poll_interval_minutes: 60
    lookback_days: 365
```

#### `extractors.teams_chats`

| Key | Type | Description |
|-----|------|-------------|
| `enabled` | `bool` | Enable or disable the Teams chats extractor. |
| `poll_interval_minutes` | `int` | Interval between syncs in worker mode. |
| `max_messages_per_chat` | `int` | Maximum number of messages to fetch per chat. |

```yaml
extractors:
  teams_chats:
    enabled: true
    poll_interval_minutes: 5
    max_messages_per_chat: 200
```

#### `extractors.teams_channels`

| Key | Type | Description |
|-----|------|-------------|
| `enabled` | `bool` | Enable or disable the Teams channels extractor. |
| `poll_interval_minutes` | `int` | Interval between syncs in worker mode. |

```yaml
extractors:
  teams_channels:
    enabled: false
    poll_interval_minutes: 5
```

#### `extractors.onedrive`

| Key | Type | Description |
|-----|------|-------------|
| `enabled` | `bool` | Enable or disable the OneDrive extractor. |
| `poll_interval_minutes` | `int` | Interval between syncs in worker mode. |
| `eager_convert_patterns` | `list[str]` | Glob patterns for files to convert immediately on sync (e.g., `["*.docx", "Reports/*.xlsx"]`). |
| `convertible_extensions` | `list[str]` | File extensions eligible for document conversion. |
| `max_file_size_mb` | `int` | Maximum file size in MB to download and convert. |

```yaml
extractors:
  onedrive:
    enabled: false
    poll_interval_minutes: 120
    eager_convert_patterns: []
    convertible_extensions: [".docx", ".pptx", ".xlsx", ".pdf", ".csv", ".txt", ".md", ".html"]
    max_file_size_mb: 100
```

#### `extractors.sharepoint`

| Key | Type | Description |
|-----|------|-------------|
| `enabled` | `bool` | Enable or disable the SharePoint extractor. |
| `poll_interval_minutes` | `int` | Interval between syncs in worker mode. |
| `eager_convert_patterns` | `list[str]` | Glob patterns for files to convert immediately on sync. |
| `convertible_extensions` | `list[str]` | File extensions eligible for document conversion. |
| `max_file_size_mb` | `int` | Maximum file size in MB to download and convert. |

```yaml
extractors:
  sharepoint:
    enabled: false
    poll_interval_minutes: 240
    eager_convert_patterns: []
    convertible_extensions: [".docx", ".pptx", ".xlsx", ".pdf", ".csv", ".txt", ".md", ".html"]
    max_file_size_mb: 100
```

#### `extractors.contacts`

| Key | Type | Description |
|-----|------|-------------|
| `enabled` | `bool` | Enable or disable the contacts extractor. |
| `poll_interval_minutes` | `int` | Interval between syncs in worker mode. |

```yaml
extractors:
  contacts:
    enabled: false
    poll_interval_minutes: 1440
```

#### `extractors.directory`

| Key | Type | Description |
|-----|------|-------------|
| `enabled` | `bool` | Enable or disable the directory extractor. |
| `poll_interval_minutes` | `int` | Interval between syncs in worker mode. |

```yaml
extractors:
  directory:
    enabled: false
    poll_interval_minutes: 10080
```

### `converters`

Document conversion settings for OneDrive and SharePoint file extractors.

#### `converters.backends`

Maps file types to conversion backends.

| Key | Type | Description |
|-----|------|-------------|
| `pdf` | `str` | Backend for PDF files. `"markitdown"` or `"native"`. |
| `docx` | `str` | Backend for Word documents. |
| `pptx` | `str` | Backend for PowerPoint presentations. |
| `xlsx` | `str` | Backend for Excel spreadsheets. |
| `csv` | `str` | Backend for CSV files. |
| `json` | `str` | Backend for JSON files. |
| `yaml` | `str` | Backend for YAML files. |
| `image` | `str` | Backend for image files. |
| `default` | `str` | Fallback backend for unrecognized file types. |

#### `converters.extraction`

| Key | Type | Description |
|-----|------|-------------|
| `timeout_seconds` | `int` | Maximum time for a single document conversion. |
| `max_file_size_mb` | `int` | Maximum file size to attempt conversion. |
| `xlsx_max_rows_per_sheet` | `int` | Row limit per sheet when converting Excel files. |

#### `converters.media`

| Key | Type | Description |
|-----|------|-------------|
| `extract_images` | `bool` | Whether to extract images from documents. |
| `image_format` | `str` | Output format for extracted images (e.g., `"png"`). |
| `image_max_dimension` | `int` | Maximum dimension for extracted images. `0` means no limit. |

```yaml
converters:
  backends:
    pdf: "markitdown"
    docx: "markitdown"
    pptx: "markitdown"
    xlsx: "markitdown"
    csv: "markitdown"
    json: "native"
    yaml: "native"
    image: "native"
    default: "native"
  extraction:
    timeout_seconds: 30
    max_file_size_mb: 100
    xlsx_max_rows_per_sheet: 500
  media:
    extract_images: false
    image_format: "png"
    image_max_dimension: 0
```
