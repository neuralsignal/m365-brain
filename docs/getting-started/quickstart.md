# Quick Start

This guide walks you through authenticating with Microsoft 365 and running your first sync.

## Azure App Registration

Before using m365-brain, you need an Azure AD (Entra ID) app registration:

1. Go to [Azure Portal > App registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade) and click **New registration**
2. Set a name (e.g., `m365-brain`)
3. Set **Supported account types** to "Accounts in this organizational directory only"
4. Set **Redirect URI** to `https://login.microsoftonline.com/common/oauth2/nativeclient` (type: Public client/native)
5. Click **Register**
6. Note the **Application (client) ID** and **Directory (tenant) ID** from the Overview page

### API Permissions

Go to **API permissions > Add a permission > Microsoft Graph > Delegated permissions** and add:

| Permission | Used by |
|-----------|---------|
| `User.Read` | All extractors (required) |
| `Mail.Read` | Email extractor |
| `Calendars.Read` | Calendar extractor |
| `Chat.Read` | Teams chats extractor |
| `ChannelMessage.Read.All` | Teams channels extractor |
| `Files.Read.All` | OneDrive extractor |
| `Sites.Read.All` | SharePoint extractor |
| `offline_access` | Token refresh (required) |

Click **Grant admin consent** if you have admin privileges, or ask your tenant admin to consent.

## Environment Variables

Set the client and tenant IDs from your app registration:

```bash
export MSAL_CLIENT_ID="your-client-id-here"
export MSAL_TENANT_ID="your-tenant-id-here"
```

Or create a `.env` file in the same directory as your `config.yaml`:

```env
MSAL_CLIENT_ID=your-client-id-here
MSAL_TENANT_ID=your-tenant-id-here
```

m365-brain automatically loads `.env` from the config file's directory.

## Authentication

Run the device code login flow:

```bash
m365-brain --config config.yaml auth login
```

This will display a message like:

```
To sign in, use a web browser to open the page https://microsoft.com/devicelogin
and enter the code XXXXXXXXX to authenticate.
```

Open the URL in your browser, enter the code, and sign in with your Microsoft 365 account. The token is cached at the path specified in `auth.token_cache_path` (default: `./state/token_cache.json`) and will be refreshed automatically on subsequent runs.

## First Sync

Run all enabled extractors once:

```bash
m365-brain --config config.yaml sync --once
```

Output:

```
  email: 142 items written
  calendar: 87 items written
  teams_chats: 23 items written
```

Your synced markdown files will appear in the configured storage location (default: `./vault/`).

## Multi-User Worker

For multi-user deployments (web mode with database), run the sync worker:

```bash
m365-brain --config config/base.yaml,config/auth.yaml,config/service/web.yaml worker
```

The worker polls the database for enabled users and their extractor preferences, then runs each (user, extractor) pair as an independent job on its configured `poll_interval_minutes`. Press `Ctrl+C` to stop.

## Extractor Selection

Run only specific extractors:

```bash
# Sync only email and calendar
m365-brain --config config.yaml sync --once --extractors email,calendar

# Sync only Teams data
m365-brain --config config.yaml sync --once --extractors teams_chats,teams_channels
```

Available extractor names: `email`, `calendar`, `teams_chats`, `teams_channels`, `onedrive`, `sharepoint`

## Next Steps

- [Configuration Reference](configuration.md) -- full config.yaml documentation
- [Extractors Guide](../user-guide/extractors.md) -- detailed extractor behavior and Graph API endpoints
- [Storage Backends](../user-guide/storage.md) -- local filesystem vs Azure Blob Storage
- [Docker](../user-guide/docker.md) -- running in containers
