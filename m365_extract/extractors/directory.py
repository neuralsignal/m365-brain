"""Directory extractor — syncs organization users via Graph API delta queries.

Reads from /users/delta for incremental sync of tenant directory.
Writes Obsidian-compatible markdown files with YAML frontmatter.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from m365_extract.config import DirectoryExtractorConfig
from m365_extract.frontmatter import DirectoryUserData, build_directory_user_frontmatter
from m365_extract.graph_client import GraphApiError, GraphClient
from m365_extract.markdown_writer import dumps_markdown, short_hash, slugify
from m365_extract.storage.base import StorageBackend

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

        if _write_user(storage, user, manager_link, direct_reports_links):
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


def _write_user(
    storage: StorageBackend,
    user: dict,
    manager_link: str,
    direct_reports_links: list[str],
) -> bool:
    """Write a single user to storage. Returns True if written."""
    user_id = user.get("id", "")
    display_name = user.get("displayName") or ""
    email = user.get("mail") or ""
    upn = user.get("userPrincipalName") or ""
    job_title = user.get("jobTitle") or ""
    department = user.get("department") or ""
    office = user.get("officeLocation") or ""
    city = user.get("city") or ""

    fm = build_directory_user_frontmatter(
        DirectoryUserData(
            display_name=display_name,
            user_id=user_id,
            email=email,
            upn=upn,
            job_title=job_title,
            department=department,
            office=office,
            city=city,
            manager_link=manager_link,
            direct_reports_links=direct_reports_links,
        )
    )

    # Build body
    body_parts = [f"# {display_name}\n", "## Profile\n"]

    if job_title:
        body_parts.append(f"- **Title:** {job_title}")
    if department:
        body_parts.append(f"- **Department:** {department}")
    if office:
        body_parts.append(f"- **Office:** {office}")
    if email:
        body_parts.append(f"- **Email:** {email}")
    if city:
        body_parts.append(f"- **City:** {city}")

    if manager_link or direct_reports_links:
        body_parts.append("\n## Organization\n")
        if manager_link:
            body_parts.append(f"- **Manager:** {manager_link}")
        if direct_reports_links:
            body_parts.append("- **Direct Reports:**")
            for link in direct_reports_links:
                body_parts.append(f"  - {link}")

    content = dumps_markdown(fm, "\n".join(body_parts))

    slug = slugify(display_name, 80)
    hsh = short_hash(user_id, 6)
    file_path = f"directory/{slug}-{hsh}/index.md"

    storage.write_file(file_path, content)
    return True
