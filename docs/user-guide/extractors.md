# Extractors

m365-brain ships with 6 extractors, each targeting a different Microsoft 365 data source. All extractors produce Obsidian-compatible markdown files with YAML frontmatter.

## Common Behavior

Every extractor follows the same contract:

- Accepts a `GraphClient`, `StorageBackend`, state dict, and extractor-specific config
- Returns `(updated_state, items_written)` -- the new state (with delta links, timestamps) and the count of files written
- Uses delta queries where the Graph API supports them, falling back to filter-based incremental sync otherwise
- HTML content is converted to markdown via `markdownify`

## Email

**Module:** `m365_brain.m365.extractors.email`
**Required scope:** `Mail.Read`
**Sync strategy:** Delta query per folder

Syncs emails from configured mail folders using Graph API delta queries. On first sync, applies a `receivedDateTime` filter based on `lookback_days`. Subsequent syncs use the stored delta link for incremental updates.

### Graph API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/me/mailFolders/{folder}/messages/delta` | GET | Delta sync of messages per folder |

### Supported Folders

`Inbox`, `SentItems`, `Drafts`, `Archive`, `DeletedItems`, `JunkEmail`

### Output Structure

```
emails/{year}/{date}/{slug}-{hash}/index.md
```

Example: `emails/2026/2026-03-15/weekly-standup-notes-a1b2c3/index.md`

### Frontmatter Fields

`subject`, `message_id`, `received_time`, `folder`, `sender_address`, `sender_name`, `to_recipients`, `importance`, `has_attachments`, `web_link`

---

## Calendar

**Module:** `m365_brain.m365.extractors.calendar`
**Required scope:** `Calendars.Read`
**Sync strategy:** Calendar view with date range

Syncs calendar events using the `calendarView` endpoint with a date range filter. Fetches events from `lookback_days` in the past to 90 days in the future.

### Graph API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/me/calendarView` | GET | Paginated calendar events within a date range |

### Output Structure

```
calendar/{year}/{month}/{date}-{slug}-{hash}.md
```

Example: `calendar/2026/2026-03/2026-03-15-standup-d4e5f6.md`

### Frontmatter Fields

`subject`, `event_id`, `start_time`, `end_time`, `location`, `organizer_name`, `organizer_email`, `attendees`, `is_recurring`, `web_link`

---

## Teams Chats

**Module:** `m365_brain.m365.extractors.teams_chats`
**Required scope:** `Chat.Read`
**Sync strategy:** Filter-based incremental (last modified datetime)

Syncs 1:1 and group chat messages. Fetches the list of chats the user participates in, then retrieves messages per chat. Uses a `lastModifiedDateTime` filter for incremental sync (not delta queries, as the chats messages endpoint does not support delta).

Each chat produces a single markdown file that is updated in place. The extractor checks whether the last message timestamp has changed before rewriting.

### Graph API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/me/chats` | GET | List user's chats (with `$expand=members`) |
| `/me/chats/{chatId}/messages` | GET | Paginated messages for a specific chat |

### Output Structure

```
teams-chats/{slug}_{hash}.md
```

Example: `teams-chats/alice-bob_g7h8i9.md`

### Frontmatter Fields

`title`, `conversation_id`, `conversation_type`, `participants`, `last_message_time`

### Body Structure

- Observations section with conversation metadata
- Relations section linking to participant contacts
- Messages section with chronologically ordered messages, each with timestamp and sender

---

## Teams Channels

**Module:** `m365_brain.m365.extractors.teams_channels`
**Required scope:** `ChannelMessage.Read.All`
**Sync strategy:** Delta query per channel

Syncs channel messages from all Teams the user has joined. Discovers teams via `/me/joinedTeams`, enumerates channels per team, then uses delta queries per channel for incremental message sync.

### Graph API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/me/joinedTeams` | GET | List teams the user is a member of |
| `/teams/{teamId}/channels` | GET | List channels in a team |
| `/teams/{teamId}/channels/{channelId}/messages/delta` | GET | Delta sync of channel messages |

### Output Structure

```
teams-channels/{team-slug}/{channel-slug}-{hash}.md
```

Example: `teams-channels/engineering/general-j0k1l2.md`

### Frontmatter Fields

`team_name`, `channel_name`, `channel_id`, `last_message_time`

### Body Structure

- Observations section with team and channel metadata
- Messages section with chronologically ordered messages

---

## OneDrive

**Module:** `m365_brain.m365.extractors.onedrive`
**Required scope:** `Files.Read.All`
**Sync strategy:** Delta query on drive root

Syncs files from the user's OneDrive using delta queries on the drive root. Tracks file IDs to handle deletions. Supports optional document conversion for Office file formats via the `converters` config.

Files matching `eager_convert_patterns` are downloaded and converted to markdown immediately. Other files get a metadata-only markdown stub.

### Graph API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/me/drive/root/delta` | GET | Delta sync of all drive items |
| `@microsoft.graph.downloadUrl` | GET | Download file content (binary) |

### Output Structure

```
onedrive/{parent-path}/{filename}.md
```

Example: `onedrive/Documents/Reports/q1-review.docx.md`

### Frontmatter Fields

`file_name`, `item_id`, `size`, `modified_time`, `modified_by`, `parent_path`, `web_url`, `conversion_status`

### Document Conversion

When a file matches `eager_convert_patterns` and its extension is in `convertible_extensions`:

1. The file is downloaded via `@microsoft.graph.downloadUrl`
2. Converted to markdown using the configured converter backend (e.g., `markitdown`)
3. The converted content replaces the metadata stub
4. `conversion_status` in frontmatter is updated to `"converted"` or `"failed"`

---

## SharePoint

**Module:** `m365_brain.m365.extractors.sharepoint`
**Required scope:** `Sites.Read.All`
**Sync strategy:** Delta query per drive, site discovery via followed sites

Syncs files from SharePoint sites the user follows. Discovers sites via `/me/followedSites`, enumerates document libraries (drives) per site, then uses delta queries per drive for incremental file sync. Shares the same file processing logic as the OneDrive extractor.

### Graph API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/me/followedSites` | GET | Discover SharePoint sites the user follows |
| `/sites/{siteId}/drives` | GET | List document libraries for a site |
| `/drives/{driveId}/root/delta` | GET | Delta sync of drive items |
| `@microsoft.graph.downloadUrl` | GET | Download file content (binary) |

### Output Structure

```
sharepoint/{site-slug}/{drive-slug}/{parent-path}/{filename}.md
```

Example: `sharepoint/intranet/shared-documents/hr/handbook.docx.md`

### Frontmatter Fields

`file_name`, `item_id`, `size`, `modified_time`, `modified_by`, `parent_path`, `web_url`, `site_name`, `drive_name`, `conversion_status`

### Site Discovery

Only sites the user actively follows are synced. To sync a SharePoint site:

1. Navigate to the site in your browser
2. Click the star/follow icon
3. The site will appear on the next sync cycle
