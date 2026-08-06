"""People frontmatter builders, and where a person's address has to live.

An address is what `ops links` resolves a dangling `[[contact-...]]` link by, and
`ops/links.py` reads one out of a candidate's **observations** -- the only
per-entity read the index offers. `m365_brain/parsers/document.py` promotes a
scalar frontmatter key to an observation and leaves a list in metadata, which no
per-entity read can reach.

A contact has N addresses, so `email` is a list, so it is metadata. The
extractor also wrote each one into the body as `- **Email:** a@example.com`,
which carries neither a `[category]` nor a `#tag` and so is ordinary prose to
`parse_observations`. The address was written twice and readable from neither
place, and the `high`-confidence address match could never be returned against a
corpus this library wrote.

The repair is the calendar and Teams one in the other shape: keep the list for a
reader, and write one body line per value -- `address_observations` below. An
**observation** rather than a relation because an address is an attribute of the
person, not an edge to another entity: `- email [[a@example.com]]` would claim a
mailbox is a note, and the link would then be reported as one more thing that
never resolves. Joining the addresses into one string would happen to read back,
since `ops.names.email_addresses` finds every address in a string -- but it is
the repair the calendar builder rejected, and one written value standing for N
is the shape that made these facts unreadable to begin with.

A **directory user** has one address, so `email` is a scalar and is promoted
already, under this same category name; so is `upn`. Nothing to repair there,
and no line is added: a second spelling of a fact the index already holds is
duplication, not a fix.
"""

from __future__ import annotations

from dataclasses import dataclass

from m365_brain.m365.frontmatter._tags import tag_slug
from m365_brain.m365.markdown_writer import now_iso, short_hash, slugify

EMAIL = "email"
"""The observation category each contact address is written under.

This extractor's vocabulary, like every frontmatter key in this module -- and
the same word the directory builder's scalar `email` key is promoted under, so
one corpus states one person's address one way. No config names it: `ops links`
reads an address by *shape* out of every observation a candidate carries,
deliberately, so that finding one never requires knowing an author's category
name.
"""

MANAGER = "manager"
"""The relation type a directory user's manager edge is written under.

A bare lowercase token like `attended_by` and `participant`, and for the same
reason: `parse_relations` reads whatever precedes the wikilink on a list item as
the edge's *type*, so `- **Manager:** [[...]]` produced an edge typed
`**Manager:**`. That edge resolves and is not silent, but no config would ever
spell it -- the first `ops.tiers.interaction_sources` entry to count managers
writes `manager`, matches nothing, and reports zero, which reads as a corpus
with no reporting lines rather than as a defect.

The frontmatter key uses the same constant, so the scalar a reader sees and the
edge the index traverses are one word, and a grep for it finds both ends.
"""


@dataclass(frozen=True)
class ContactData:
    display_name: str
    contact_id: str
    email_addresses: list[str]
    phones: list[str]
    company: str
    job_title: str
    department: str
    categories: list[str]


@dataclass(frozen=True)
class DirectoryUserData:
    display_name: str
    user_id: str
    email: str
    upn: str
    job_title: str
    department: str
    office: str
    city: str
    manager_link: str
    direct_reports_links: list[str]


def build_contact_frontmatter(data: ContactData) -> dict:
    """Build frontmatter dict for a contact."""
    slug = slugify(data.display_name, 80)
    permalink = f"contact-{slug}-{short_hash(data.contact_id, 6)}"
    tags = ["contact"]
    tags.extend(tag for tag in (tag_slug(c, 80) for c in data.categories) if tag)
    fm: dict = {
        "title": data.display_name,
        "permalink": permalink,
        "type": "contact",
        "tags": tags,
        "source": {
            "system": "microsoft365",
            "service": "people",
            "id": data.contact_id,
            # A contact has no web link upstream. The key is still here because
            # `source` has to have one shape across every entity type.
            "url": None,
            "extracted_at": now_iso(),
            "extractor": "m365-brain/contacts/1.1",
        },
    }
    if data.email_addresses:
        fm["email"] = data.email_addresses
    if data.phones:
        fm["phone"] = data.phones
    if data.company:
        fm["company"] = data.company
    if data.job_title:
        fm["job_title"] = data.job_title
    if data.department:
        fm["department"] = data.department
    return fm


def address_observations(data: ContactData) -> list[str]:
    """One `- [email] a@example.com` line per address.

    Written into the markdown body, because `email` is a list up there and a
    list is metadata -- see the module docstring. A blank address is dropped
    rather than emitted as an empty category, which would index a statement
    saying nothing.
    """
    return [f"- [{EMAIL}] {address}" for address in data.email_addresses if address]


def build_directory_user_frontmatter(data: DirectoryUserData) -> dict:
    """Build frontmatter dict for a directory user."""
    slug = slugify(data.display_name, 80)
    permalink = f"directory-{slug}-{short_hash(data.user_id, 6)}"
    tags = ["directory"]
    department_tag = tag_slug(data.department, 80)
    if department_tag:
        tags.append(department_tag)
    fm: dict = {
        "title": data.display_name,
        "permalink": permalink,
        "type": "directory_user",
        "tags": tags,
        "email": data.email,
        "upn": data.upn,
        "source": {
            "system": "microsoft365",
            "service": "directory",
            "id": data.user_id,
            "url": None,
            "extracted_at": now_iso(),
            "extractor": "m365-brain/directory/1.0",
        },
    }
    if data.job_title:
        fm["job_title"] = data.job_title
    if data.department:
        fm["department"] = data.department
    if data.office:
        fm["office"] = data.office
    if data.city:
        fm["city"] = data.city
    if data.manager_link:
        fm[MANAGER] = data.manager_link
    if data.direct_reports_links:
        fm["direct_reports"] = data.direct_reports_links
    return fm
