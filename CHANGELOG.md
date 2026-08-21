# Changelog

## [1.2.2](https://github.com/neuralsignal/m365-brain/compare/v1.2.1...v1.2.2) (2026-08-21)


### Bug Fixes

* add missing return type annotations in commands and config_loader ([#303](https://github.com/neuralsignal/m365-brain/issues/303)) ([#306](https://github.com/neuralsignal/m365-brain/issues/306)) ([a4733bf](https://github.com/neuralsignal/m365-brain/commit/a4733bf05b945a47bd162b3104ce224567113fe0))

## [1.2.1](https://github.com/neuralsignal/m365-brain/compare/v1.2.0...v1.2.1) (2026-08-19)


### Bug Fixes

* enforce attachment_root boundary in resolve_attachment ([#299](https://github.com/neuralsignal/m365-brain/issues/299)) ([#300](https://github.com/neuralsignal/m365-brain/issues/300)) ([4b8d8b6](https://github.com/neuralsignal/m365-brain/commit/4b8d8b63cf3e459e889286542e701075d65dde95))

## [1.2.0](https://github.com/neuralsignal/m365-brain/compare/v1.1.1...v1.2.0) (2026-08-10)


### Features

* consolidate five units into m365-brain ([4d7eb21](https://github.com/neuralsignal/m365-brain/commit/4d7eb214c177e5116749edcb1e46b17fd6b1fb26))
* file catalogue, three silent-config fixes, and publication readiness ([b349830](https://github.com/neuralsignal/m365-brain/commit/b34983070d1af66b7aadecc01a37853baf1912da))
* knowledge layer, Graph transport, and the vault path contract ([2f587f6](https://github.com/neuralsignal/m365-brain/commit/2f587f6b200e84059985b9f2ca6387e494b8a75d))
* make the file catalogue real, close three silent-config defects, and scrub for publication ([1968fbd](https://github.com/neuralsignal/m365-brain/commit/1968fbd9111e9704f812818d71445027b2f7d87d))
* one config root covering every knob the port needs ([83c0a68](https://github.com/neuralsignal/m365-brain/commit/83c0a68b81a0bae1b4cedecb5721b0d7321e6ff0))
* runtime, CLI, and the three bundled skills ([bf1401a](https://github.com/neuralsignal/m365-brain/commit/bf1401a7bf2de4dce649a0f88f610319f515c07f))
* the typed outbox, Graph file writes, and auth profiles ([dcaa3a1](https://github.com/neuralsignal/m365-brain/commit/dcaa3a11127dc78cecbc38db2c32ac87dc98e63f))


### Bug Fixes

* $top on a delta query is the item budget, not a page size ([#264](https://github.com/neuralsignal/m365-brain/issues/264)) ([81912ca](https://github.com/neuralsignal/m365-brain/commit/81912ca62c9c4cf44bb66442cd697312f82ac291))
* bump pypdf lower bound to 6.15.0 for CVE-2026-71852 and CVE-2026-71870 ([#277](https://github.com/neuralsignal/m365-brain/issues/277)) ([#281](https://github.com/neuralsignal/m365-brain/issues/281)) ([3cbe17d](https://github.com/neuralsignal/m365-brain/commit/3cbe17def0305412d99574261d0656bd57b9ab9e))
* drop email lookback_days, a window Graph never applied ([#266](https://github.com/neuralsignal/m365-brain/issues/266)) ([df0a98f](https://github.com/neuralsignal/m365-brain/commit/df0a98f99e8def9d6583cf4c1ae644f8c523d0cc))
* keep logs off stdout in every verb, not just two ([cf242d9](https://github.com/neuralsignal/m365-brain/commit/cf242d94f4a8d53f94db1b6a9e5306c92486b8d3))
* make a capped answer say it was capped ([a319694](https://github.com/neuralsignal/m365-brain/commit/a319694389fcb30ea89746ed80c9636e7f6fcfc2))
* make a contact's address resolvable by ops links ([ce90a00](https://github.com/neuralsignal/m365-brain/commit/ce90a00eb8acc6e541cb0a02654ace2906ed660e))
* make a contact's address resolvable by ops links ([70e92e7](https://github.com/neuralsignal/m365-brain/commit/70e92e760c9ca551d6bc9d3701f80eb5cf88bf53))
* make a Teams chat's participants countable by ops tiers ([a94be26](https://github.com/neuralsignal/m365-brain/commit/a94be26eb4f59ec942f8510c818225104289ffb8))
* make a Teams chat's participants countable by ops tiers ([b64ebd7](https://github.com/neuralsignal/m365-brain/commit/b64ebd74a1002207596f1c6a37bcacd3cda6a335))
* make every config knob bind, or take it out ([e7ea191](https://github.com/neuralsignal/m365-brain/commit/e7ea1918948f7c308256e57346d66a8fa1a13ba8))
* resolve every printed path, and accept one back ([36cdb69](https://github.com/neuralsignal/m365-brain/commit/36cdb6908b0a18d33e6a1aa83bff6608a697a491))
* scope the prune to the roots a run actually walked ([26b8294](https://github.com/neuralsignal/m365-brain/commit/26b8294f0fab3fc7d67d112b2913e0909e5938e3))
* separate two status vocabularies, give relations spellable types ([#265](https://github.com/neuralsignal/m365-brain/issues/265)) ([83081a4](https://github.com/neuralsignal/m365-brain/commit/83081a41ebf8cf1be10b6868290378d85dda0eb8))
* stop answering three different questions with exit 3 ([d13236b](https://github.com/neuralsignal/m365-brain/commit/d13236b757677fcf68c0c6505c6e8b3379768c0f))
* stop four words from carrying two vocabularies each ([a076ba6](https://github.com/neuralsignal/m365-brain/commit/a076ba63ce5a3505e3c4c12f91e4c3a45fc255c6))


### Documentation

* add INTENT.md, CONTRACTS.md, and ADRs 0001-0012 ([b335ef0](https://github.com/neuralsignal/m365-brain/commit/b335ef034f10e33cd813e71085a1ec987a74c685))
* author the deferred backlog ([51d05fd](https://github.com/neuralsignal/m365-brain/commit/51d05fdf35bd58f06f2fb261e2e6e1bbccaed0f9))
* bring in the knowledge-layer design docs and split the test tree ([215fd50](https://github.com/neuralsignal/m365-brain/commit/215fd50a8f3d5b2b5c21aa9933c07caa6481d647))
* stop the shipped documents describing a CLI that is not there ([347f01f](https://github.com/neuralsignal/m365-brain/commit/347f01f716a56f75b200364e7014b252624ef524))
* task for an application-permission auth profile ([4d59fe2](https://github.com/neuralsignal/m365-brain/commit/4d59fe269abb550ac68bc661457e431e19e88c92))

## [1.1.1](https://github.com/neuralsignal/m365-extract/compare/v1.1.0...v1.1.1) (2026-08-04)


### Bug Fixes

* add leading dot to svc.ms in SSRF domain allowlist ([#182](https://github.com/neuralsignal/m365-extract/issues/182)) ([d0c5cb7](https://github.com/neuralsignal/m365-extract/commit/d0c5cb7612fc3075928aceb95a1fa2db9bbfa450))
* catch GraphApiError instead of dead httpx.HTTPStatusError in _file_helpers.py ([#184](https://github.com/neuralsignal/m365-extract/issues/184)) ([a65f640](https://github.com/neuralsignal/m365-extract/commit/a65f6407f177cca47a353bc390c985719a88f8d6))
* consume OAuth CSRF state token after verification to prevent replay ([#245](https://github.com/neuralsignal/m365-extract/issues/245)) ([219b89d](https://github.com/neuralsignal/m365-extract/commit/219b89d9d0c9ba87a734740ba14e9fa74eee6942))
* eliminate TOCTOU race in token cache file permissions ([#239](https://github.com/neuralsignal/m365-extract/issues/239)) ([d10aa75](https://github.com/neuralsignal/m365-extract/commit/d10aa752abcfd62af26ff9427dc13ab6c2a1b88d))
* remove type: ignore suppressions in _teams_ingest.py with assert narrowing ([#256](https://github.com/neuralsignal/m365-extract/issues/256)) ([897d39c](https://github.com/neuralsignal/m365-extract/commit/897d39cee865527f7b903a176b9c0b66bcdf9157))
* replace type: ignore with sqlalchemy.desc() in admin_state.py ([#189](https://github.com/neuralsignal/m365-extract/issues/189)) ([05ca1b4](https://github.com/neuralsignal/m365-extract/commit/05ca1b4e52da3895b056dc78c39b465ea1f69f30))
* store OAuth CSRF state as per-token files ([#244](https://github.com/neuralsignal/m365-extract/issues/244)) ([951bfea](https://github.com/neuralsignal/m365-extract/commit/951bfea8f017c34b7746e2881f5920581d852afa))
* update stale test call sites for consolidated refactors ([a75a0e5](https://github.com/neuralsignal/m365-extract/commit/a75a0e58dbd3d55a8ee16828c78e16d0467d7a2e))

## [1.1.0](https://github.com/neuralsignal/m365-extract/compare/v1.0.0...v1.1.0) (2026-06-16)


### Features

* adopt obsidian-import 1.2.0 public API and extraction isolation ([8773a1d](https://github.com/neuralsignal/m365-extract/commit/8773a1d91197e887c3ead46b784ec1721cd82e05))
* merge-based incremental Teams sync with whole-history retention ([e0d9378](https://github.com/neuralsignal/m365-extract/commit/e0d9378f4a5d87925ee72d2e89830d31ebd56f93))
* skip non-file Teams attachment types and stop retrying permanent download failures ([912396a](https://github.com/neuralsignal/m365-extract/commit/912396acfae1874c738ba7b4064fbc5bc9effeca))
* **teams_chats:** download file attachments and inline images ([6eae19c](https://github.com/neuralsignal/m365-extract/commit/6eae19c31852fe5f0ab1d68030718a197e14bc93))


### Bug Fixes

* add missing teams_chats attachment fields to admin config test fixtures ([fcd8bb0](https://github.com/neuralsignal/m365-extract/commit/fcd8bb04f29a33e3af7bda6ed363e9e6a390666d))
* add missing teams_chats attachment fields to test config fixtures ([8638edf](https://github.com/neuralsignal/m365-extract/commit/8638edf0cf91518b19955d8f96f71a24f0e87a04))
* escape single quotes in OData folder filter to prevent injection ([#214](https://github.com/neuralsignal/m365-extract/issues/214)) ([a967321](https://github.com/neuralsignal/m365-extract/commit/a9673216fd584b38c9eeb1ac28b2cbdafc95d415))
* re-raise unexpected exceptions in worker instead of swallowing ([#207](https://github.com/neuralsignal/m365-extract/issues/207)) ([d22f9cc](https://github.com/neuralsignal/m365-extract/commit/d22f9cc58f21d885ece271f533f8db55e54a583b))
* remove unused _access_token state var storing raw bearer token ([#215](https://github.com/neuralsignal/m365-extract/issues/215)) ([48ab25f](https://github.com/neuralsignal/m365-extract/commit/48ab25f6c250468d0efdf260266cd503be341e76))
* validate user_id as UUID before filesystem path construction ([#216](https://github.com/neuralsignal/m365-extract/issues/216)) ([26ecb6f](https://github.com/neuralsignal/m365-extract/commit/26ecb6f3af79d69aa7c32317525fdea66c1d3eb5))

## [1.0.0](https://github.com/neuralsignal/m365-extract/compare/v0.3.0...v1.0.0) (2026-05-14)


### ⚠ BREAKING CHANGES

* EmailExtractorConfig.folders is replaced by a mailboxes list. Each entry has address (use "me" for /me/ endpoints, or a UPN for /users/{address}/ endpoints), folders (list or null for auto-discovery), and output_subdir (storage namespace under emails/).

### Features

* support multiple mailboxes in email extractor ([d65700f](https://github.com/neuralsignal/m365-extract/commit/d65700f7ef84204a39e58f7a88c85c3b6b4f37a7))


### Bug Fixes

* add server-side admin authorization to AdminState handlers ([#169](https://github.com/neuralsignal/m365-extract/issues/169)) ([4064034](https://github.com/neuralsignal/m365-extract/commit/4064034648e704eefbe1243e66fee84a185043ac))
* align tests with module split and apply ruff format/imports ([16f95c4](https://github.com/neuralsignal/m365-extract/commit/16f95c4d7d2842653e8bd479e3ce38d04292004a))
* **email:** drop wellKnownName from auto-discover (v1.0 incompatible) ([953b6c8](https://github.com/neuralsignal/m365-extract/commit/953b6c8725d80035b848246beb158e5c23ee7d10))
* **email:** drop wellKnownName from auto-discover select (v1.0) ([ccea9fc](https://github.com/neuralsignal/m365-extract/commit/ccea9fcc4f79160960d74646a49dba2d2f68be61))
* narrow SSRF allowlist by replacing .windows.net with specific CDN domains ([#171](https://github.com/neuralsignal/m365-extract/issues/171)) ([e4bd3d9](https://github.com/neuralsignal/m365-extract/commit/e4bd3d9ba4c967e276a88a6fa6b72bc03222b074))
* pin pillow &gt;=12.2.0 to remediate CVE-2026-40192 ([#127](https://github.com/neuralsignal/m365-extract/issues/127)) ([509ab6d](https://github.com/neuralsignal/m365-extract/commit/509ab6dfe3a65baeaa97fa9b30693d9ce75a9d5e))
* sanitize attachment filename to basename to prevent path traversal ([#170](https://github.com/neuralsignal/m365-extract/issues/170)) ([f8ad054](https://github.com/neuralsignal/m365-extract/commit/f8ad054627a16ccb353da3422fe1b0ff9f73a10c))

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

### Breaking Changes

* `m365_extract.frontmatter` builders now take a single dataclass argument instead of keyword arguments. Replace `build_email_frontmatter(subject=..., message_id=..., ...)` with `build_email_frontmatter(EmailData(subject=..., message_id=..., ...))`. Same shape for `build_calendar_frontmatter` / `CalendarEventData`, `build_contact_frontmatter` / `ContactData`, `build_directory_user_frontmatter` / `DirectoryUserData`, `build_onedrive_frontmatter` / `OneDriveFileData`, `build_sharepoint_frontmatter` / `SharePointFileData`, `build_teams_chat_frontmatter` / `TeamsChatData`, and `build_teams_channel_frontmatter` / `TeamsChannelData`. All dataclass fields are required — callers must pass every field explicitly.

### Security

* Pin `pyopenssl>=26.0,<27` to fix CVE-2026-27448 (TLS callback bypass) and CVE-2026-27459 (buffer overflow)
* Upgrade `pypdf` from `6.9.2` to `6.10.0` to fix CVE-2026-40260 (memory DoS via crafted PDF XMP metadata, GHSA-3crg-w4f6-42mx)
* Track CVE-2026-3219 (pip ZIP/tar archive confusion, GHSA-58qw-9mgm-455v, MODERATE) — no fix released yet; pin will be narrowed when a patched pip version is available

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
