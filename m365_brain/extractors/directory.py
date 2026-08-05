"""Directory extractor — syncs organization users via Graph API delta queries.

Reads from /users/delta for incremental sync of tenant directory.
Writes Obsidian-compatible markdown files with YAML frontmatter.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from m365_brain.config import DirectoryExtractorConfig
from m365_brain.frontmatter import DirectoryUserData, build_directory_user_frontmatter
from m365_brain.graph_client import GraphApiError, GraphClient
from m365_brain.markdown_writer import dumps_markdown, short_hash, slugify
from m365_brain.storage.base import StorageBackend

log = structlog.get_logger()

name = "directory"
required_scopes = ["User.Read.All", "Directory.Read.All"]

_USER_SELECT = (
    "id,displayName,givenName,surname,mail,userPrincipalName,"
    "jobTitle,department,officeLocation,companyName,businessPhones,"
    "mobilePhone,city,state,country,accountEnabled,"
    "createdDateTime"
)


def run(
    client: GraphClient,
    storage: StorageBackend,
    state: dict,
    config: DirectoryExtractorConfig,
) -> tuple[dict, int]:
    """Extract directory users using delta queries.

    Returns (updated_state, total_items_written).
    """
    delta_link = state.get("delta_link")
    path = "/users/delta"

    params: dict[str, str] = {"$select": _USER_SELECT, "$top": "50"}
    if config.only_active_users and not delta_link:
        params["$filter"] = "accountEnabled eq true"

    users, new_delta_link = client.get_delta(path, delta_link, params=params, max_pages=client.max_pages)

    if new_delta_link:
        state["delta_link"] = new_delta_link

    written = 0
    for user in users:
        if _should_skip_user(user, config.only_active_users):
            continue

        manager_link = ""
        if config.include_manager_chain:
            manager_link = _fetch_manager_link(client, user["id"])

        direct_reports_links: list[str] = []
        if config.include_direct_reports:
            direct_reports_links = _fetch_direct_reports_links(client, user["id"])

        user_data = _extract_user_data(user, manager_link, direct_reports_links)
        if _write_user(storage, user_data):
            written += 1

    state["last_sync"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    log.info("directory.sync_complete", total_written=written)
    return state, written


def _should_skip_user(user: dict, only_active: bool) -> bool:
    """Check if a user should be skipped."""
    user_id = user.get("id", "")
    display_name = user.get("displayName") or ""

    if not user_id or not display_name:
        log.warning("directory.skipping_invalid", user_id=user_id)
        return True

    return only_active and not user.get("accountEnabled", True)


def _build_user_link(user: dict) -> str:
    """Build an Obsidian wiki-link for a user."""
    display_name = user.get("displayName") or ""
    user_id = user.get("id") or ""
    if not display_name or not user_id:
        return ""
    slug = slugify(display_name, 80)
    hsh = short_hash(user_id, 6)
    return f"[[directory-{slug}-{hsh}]]"


def _fetch_manager_link(client: GraphClient, user_id: str) -> str:
    """Fetch a user's manager and return an Obsidian wiki-link."""
    try:
        manager = client.get(f"/users/{user_id}/manager", params={"$select": "id,displayName"})
        return _build_user_link(manager)
    except GraphApiError:
        log.debug("directory.no_manager", user_id=user_id)
        return ""


def _fetch_direct_reports_links(client: GraphClient, user_id: str) -> list[str]:
    """Fetch a user's direct reports and return Obsidian wiki-links."""
    try:
        reports = list(
            client.get_paginated(
                f"/users/{user_id}/directReports",
                params={"$select": "id,displayName"},
                max_pages=client.max_pages,
            )
        )
        links = []
        for report in reports:
            link = _build_user_link(report)
            if link:
                links.append(link)
        return links
    except GraphApiError:
        log.debug("directory.no_direct_reports", user_id=user_id)
        return []


def _extract_user_data(user: dict, manager_link: str, direct_reports_links: list[str]) -> DirectoryUserData:
    """Extract and normalize directory user data from a Graph API user dict."""
    return DirectoryUserData(
        display_name=user.get("displayName") or "",
        user_id=user.get("id", ""),
        email=user.get("mail") or "",
        upn=user.get("userPrincipalName") or "",
        job_title=user.get("jobTitle") or "",
        department=user.get("department") or "",
        office=user.get("officeLocation") or "",
        city=user.get("city") or "",
        manager_link=manager_link,
        direct_reports_links=direct_reports_links,
    )


def _write_user(storage: StorageBackend, data: DirectoryUserData) -> bool:
    """Build frontmatter and markdown body for a directory user, then write to storage."""
    fm = build_directory_user_frontmatter(data)

    body_parts = [f"# {data.display_name}\n", "## Profile\n"]

    if data.job_title:
        body_parts.append(f"- **Title:** {data.job_title}")
    if data.department:
        body_parts.append(f"- **Department:** {data.department}")
    if data.office:
        body_parts.append(f"- **Office:** {data.office}")
    if data.email:
        body_parts.append(f"- **Email:** {data.email}")
    if data.city:
        body_parts.append(f"- **City:** {data.city}")

    if data.manager_link or data.direct_reports_links:
        body_parts.append("\n## Organization\n")
        if data.manager_link:
            body_parts.append(f"- **Manager:** {data.manager_link}")
        if data.direct_reports_links:
            body_parts.append("- **Direct Reports:**")
            for link in data.direct_reports_links:
                body_parts.append(f"  - {link}")

    content = dumps_markdown(fm, "\n".join(body_parts))

    slug = slugify(data.display_name, 80)
    hsh = short_hash(data.user_id, 6)
    file_path = f"directory/{slug}-{hsh}/index.md"

    storage.write_file(file_path, content)
    return True
