"""Tests for contacts extractor."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from m365_brain.config import ContactsExtractorConfig, GraphConfig
from m365_brain.m365.client import GraphClient
from m365_brain.m365.extractors import contacts
from m365_brain.m365.markdown_writer import loads_markdown
from m365_brain.storage.local import LocalBackend

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures"


@pytest.fixture()
def contacts_config():
    return ContactsExtractorConfig(
        enabled=True,
        poll_interval_minutes=1440,
        max_items_per_sync=500,
        include_contact_folders=False,
    )


@pytest.fixture()
def graph_config():
    return GraphConfig(
        max_retries=1,
        backoff_base_ms=10,
        timeout_seconds=5,
        max_pages=10,
        max_retry_after_seconds=300.0,
        error_message_max_length=200,
    )


@pytest.fixture()
def contacts_response():
    return json.loads((FIXTURES_DIR / "contacts_response.json").read_text())


class TestContactsExtractor:
    def test_sync_produces_markdown(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, contacts_config, contacts_response, ctx
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/contacts/delta.*"),
            json=contacts_response,
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = contacts.run(client, storage, {}, contacts_config, ctx)

        assert count == 2
        assert "delta_link" in state
        assert "last_sync" in state

        files = storage.list_files(ctx.paths.inbox_root("contacts"))
        assert len(files) == 2

        client.close()

    def test_incremental_sync_uses_delta_link(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, contacts_config, ctx
    ):
        delta_url = "https://graph.microsoft.com/v1.0/me/contacts/delta?$deltatoken=existing"
        httpx_mock.add_response(
            url=delta_url,
            json={
                "value": [
                    {
                        "id": "contact-new",
                        "displayName": "New Person",
                        "emailAddresses": [{"address": "new@example.com"}],
                        "businessPhones": [],
                        "mobilePhone": None,
                        "companyName": "",
                        "jobTitle": "",
                        "department": "",
                        "categories": [],
                    }
                ],
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/contacts/delta?$deltatoken=new",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        existing_state = {"delta_link": delta_url}
        state, count = contacts.run(client, storage, existing_state, contacts_config, ctx)

        assert count == 1
        assert state["delta_link"] == "https://graph.microsoft.com/v1.0/me/contacts/delta?$deltatoken=new"
        client.close()

    def test_empty_response(self, httpx_mock: HTTPXMock, tmp_path, graph_config, contacts_config, ctx):
        httpx_mock.add_response(
            url=re.compile(r".*/me/contacts/delta.*"),
            json={"value": [], "@odata.deltaLink": "https://delta?token=empty"},
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = contacts.run(client, storage, {}, contacts_config, ctx)
        assert count == 0
        client.close()

    def test_skips_contacts_without_display_name(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, contacts_config, ctx
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/contacts/delta.*"),
            json={
                "value": [
                    {"id": "no-name", "displayName": ""},
                    {"id": "", "displayName": "No ID"},
                ],
                "@odata.deltaLink": "https://delta?token=skip",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = contacts.run(client, storage, {}, contacts_config, ctx)
        assert count == 0
        client.close()

    def test_contact_markdown_content(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, contacts_config, contacts_response, ctx
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/contacts/delta.*"),
            json=contacts_response,
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        contacts.run(client, storage, {}, contacts_config, ctx)

        files = storage.list_files(ctx.paths.inbox_root("contacts"))
        content = storage.read_file(files[0])
        meta, body = loads_markdown(content)

        assert meta["type"] == "contact"
        assert meta["source"]["service"] == "people"
        assert meta["source"]["extractor"] == "m365-brain/contacts/1.1"
        assert "# " in body
        client.close()

    def test_addresses_are_observation_lines_rather_than_prose(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, contacts_config, contacts_response, ctx
    ):
        """One `- [email] ...` line per address, including for the contact that has two.

        `- **Email:** ...` carries no `[category]` and no `#tag`, so it parsed as
        an ordinary bullet; the frontmatter `email` key is a list, so it stays in
        metadata. The address was written twice and readable from neither, which
        is why `ops links` could never return its address match.
        """
        httpx_mock.add_response(url=re.compile(r".*/me/contacts/delta.*"), json=contacts_response)

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        contacts.run(client, storage, {}, contacts_config, ctx)

        written = "\n".join(
            loads_markdown(storage.read_file(path))[1] for path in storage.list_files(ctx.paths.inbox_root("contacts"))
        )

        assert "**Email:**" not in written
        assert sorted(line for line in written.splitlines() if line.startswith("- [email] ")) == [
            "- [email] bob.chen@contoso.com",
            "- [email] bob@example.com",
            "- [email] jane@contoso.com",
        ]
        client.close()

    def test_everything_fetched_is_processed_no_post_hoc_slice(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, ctx
    ):
        """B2: max_items_per_sync bounds the page budget; nothing fetched is sliced away and lost."""
        config = ContactsExtractorConfig(
            enabled=True,
            poll_interval_minutes=1440,
            max_items_per_sync=1,
            include_contact_folders=False,
        )

        httpx_mock.add_response(
            url=re.compile(r".*/me/contacts/delta.*"),
            json={
                "value": [
                    {
                        "id": "c1",
                        "displayName": "Contact One",
                        "emailAddresses": [],
                        "businessPhones": [],
                        "categories": [],
                    },
                    {
                        "id": "c2",
                        "displayName": "Contact Two",
                        "emailAddresses": [],
                        "businessPhones": [],
                        "categories": [],
                    },
                ],
                "@odata.deltaLink": "https://delta?token=cap",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = contacts.run(client, storage, {}, config, ctx)
        assert count == 2
        assert state["delta_link"] == "https://delta?token=cap"
        client.close()

    def test_capped_fetch_resumes_from_pending_next_link(self, httpx_mock: HTTPXMock, tmp_path, graph_config, ctx):
        """B2 regression: a delta round capped mid-way resumes next cycle; the tail is never skipped."""
        config = ContactsExtractorConfig(
            enabled=True,
            poll_interval_minutes=1440,
            max_items_per_sync=1,
            include_contact_folders=False,
        )
        pending = "https://graph.microsoft.com/v1.0/me/contacts/delta?$skiptoken=tail"
        httpx_mock.add_response(
            url=re.compile(r".*/me/contacts/delta$"),
            json={
                "value": [
                    {
                        "id": "c1",
                        "displayName": "Contact One",
                        "emailAddresses": [],
                        "businessPhones": [],
                        "categories": [],
                    }
                ],
                "@odata.nextLink": pending,
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = contacts.run(client, storage, {}, config, ctx)
        assert count == 1
        assert state["delta_link"] == pending

        httpx_mock.add_response(
            url=pending,
            json={
                "value": [
                    {
                        "id": "c2",
                        "displayName": "Contact Two",
                        "emailAddresses": [],
                        "businessPhones": [],
                        "categories": [],
                    }
                ],
                "@odata.deltaLink": "https://delta?token=final",
            },
        )
        state, count = contacts.run(client, storage, state, config, ctx)

        assert count == 1
        assert state["delta_link"] == "https://delta?token=final"
        assert len(storage.list_files(ctx.paths.inbox_root("contacts"))) == 2
        client.close()

    def test_contact_folders_sync(self, httpx_mock: HTTPXMock, tmp_path, graph_config, ctx):
        config = ContactsExtractorConfig(
            enabled=True,
            poll_interval_minutes=1440,
            max_items_per_sync=500,
            include_contact_folders=True,
        )

        # Default contacts delta
        httpx_mock.add_response(
            url=re.compile(r".*/me/contacts/delta.*"),
            json={"value": [], "@odata.deltaLink": "https://delta?token=default"},
        )

        # Contact folders list
        httpx_mock.add_response(
            url=re.compile(r".*/me/contactFolders\?.*"),
            json={
                "value": [{"id": "folder-1", "displayName": "Work"}],
            },
        )

        # Folder contacts delta
        httpx_mock.add_response(
            url=re.compile(r".*/me/contactFolders/folder-1/contacts/delta.*"),
            json={
                "value": [
                    {
                        "id": "fc-1",
                        "displayName": "Folder Contact",
                        "emailAddresses": [{"address": "fc@example.com"}],
                        "businessPhones": [],
                        "categories": [],
                    }
                ],
                "@odata.deltaLink": "https://delta?token=folder",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = contacts.run(client, storage, {}, config, ctx)
        assert count == 1
        assert "delta_link_folder_folder-1" in state
        client.close()

    def test_contact_folders_skips_folder_without_id(self, httpx_mock: HTTPXMock, tmp_path, graph_config, ctx):
        config = ContactsExtractorConfig(
            enabled=True,
            poll_interval_minutes=1440,
            max_items_per_sync=500,
            include_contact_folders=True,
        )

        httpx_mock.add_response(
            url=re.compile(r".*/me/contacts/delta.*"),
            json={"value": [], "@odata.deltaLink": "https://delta?token=default"},
        )

        httpx_mock.add_response(
            url=re.compile(r".*/me/contactFolders\?.*"),
            json={
                "value": [
                    {"displayName": "No ID Folder"},
                    {"id": "", "displayName": "Empty ID Folder"},
                ],
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = contacts.run(client, storage, {}, config, ctx)
        assert count == 0
        assert storage.list_files(ctx.paths.inbox_root("contacts")) == []
        client.close()

    def test_personal_notes_in_body(self, httpx_mock: HTTPXMock, tmp_path, graph_config, contacts_config, ctx):
        httpx_mock.add_response(
            url=re.compile(r".*/me/contacts/delta.*"),
            json={
                "value": [
                    {
                        "id": "notes-contact",
                        "displayName": "Notes Person",
                        "emailAddresses": [],
                        "businessPhones": [],
                        "personalNotes": "Important collaborator on Project X.",
                        "categories": [],
                    }
                ],
                "@odata.deltaLink": "https://delta?token=notes",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        contacts.run(client, storage, {}, contacts_config, ctx)

        files = storage.list_files(ctx.paths.inbox_root("contacts"))
        content = storage.read_file(files[0])
        assert "Important collaborator on Project X." in content
        client.close()


class TestExtractContactData:
    """Tests for _extract_contact_data pure extraction function."""

    def test_extracts_full_contact(self):
        contact = {
            "id": "c-001",
            "displayName": "Jane Doe",
            "emailAddresses": [{"address": "jane@example.com"}],
            "businessPhones": ["+1-555-0100"],
            "mobilePhone": "+1-555-0101",
            "companyName": "Acme Corp",
            "jobTitle": "Engineer",
            "department": "R&D",
            "categories": ["VIP"],
            "personalNotes": "Met at conference.",
        }

        extracted = contacts._extract_contact_data(contact)

        assert extracted is not None
        data, notes = extracted
        assert data.contact_id == "c-001"
        assert data.display_name == "Jane Doe"
        assert data.email_addresses == ["jane@example.com"]
        assert data.phones == ["+1-555-0100", "+1-555-0101"]
        assert data.company == "Acme Corp"
        assert data.job_title == "Engineer"
        assert data.department == "R&D"
        assert data.categories == ["VIP"]
        assert notes == "Met at conference."

    def test_returns_none_for_missing_id(self):
        contact = {"id": "", "displayName": "Someone"}
        assert contacts._extract_contact_data(contact) is None

    def test_returns_none_for_missing_display_name(self):
        contact = {"id": "c-002", "displayName": ""}
        assert contacts._extract_contact_data(contact) is None

    def test_handles_minimal_contact(self):
        contact = {
            "id": "c-003",
            "displayName": "Minimal",
            "emailAddresses": [],
            "businessPhones": [],
        }

        extracted = contacts._extract_contact_data(contact)

        assert extracted is not None
        data, notes = extracted
        assert data.email_addresses == []
        assert data.phones == []
        assert data.company == ""
        assert notes == ""


class TestOddLayoutGoldenPaths:
    def test_contacts_land_under_the_configured_names_only(
        self, httpx_mock: HTTPXMock, local_storage, graph_config, contacts_config, odd_ctx, contacts_response
    ):
        """Golden keys under a layout that shares no name with the conventional one."""
        httpx_mock.add_response(url=re.compile(r".*/me/contacts/delta.*"), json=contacts_response)
        client = GraphClient(graph_config, lambda: "test-token")

        _state, count = contacts.run(client, local_storage, {}, contacts_config, odd_ctx)

        assert count == 2
        assert local_storage.list_files("zz-inbox") == [
            "zz-inbox/address-book/bob-chen-214b9c/page.md",
            "zz-inbox/address-book/jane-smith-f2dc23/page.md",
        ]
        assert local_storage.list_files("inbox") == []
        client.close()
