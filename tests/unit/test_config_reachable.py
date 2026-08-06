"""Every declared config field is reachable from a line that consumes it.

A key in the schema is a promise. `extra="forbid"` makes an operator spell it,
and `SECTION_MODEL_CONFIG` makes every field required, so a declared field is
work an adopter must do -- mint a secret, pick a number, decide a policy. A
field nothing reads charges that price for nothing and, worse, reads as a
switch: `service.mode` was a required string documented as accepting only
`"cli"`, and `mode: "banana"` loaded clean.

Six were found and removed this way: `service.mode`, `converters.slug_max_length`
and `.hash_length` (fifteen call sites pass the literals 80 and 6, and the
section is forwarded to `obsidian_import`, which drops unknown keys silently),
`web.host` / `.port` / `.secret_key` / `.session_timeout_minutes` -- the last a
**required** `SecretStr` for a consumer that did not exist -- and the whole
`ops.tiers.write_back` subtree.

**How reachability is judged.** A field name counts as consumed if it appears
anywhere in `m365_brain/`, `m365_admin/` or the installed `obsidian_import` as
an attribute access (`config.graph.max_pages`), a subscript (`data["backends"]`)
or a keyword argument. That is deliberately generous: this test is a floor, not
a proof. It cannot tell a field that binds from one that is merely mentioned --
`extractors.contacts.max_items_per_sync` was read on every cycle and could still
never reach the server -- so a name appearing here is the weakest evidence that
counts, and the strongest a static walk can give.

`obsidian_import` is walked rather than allow-listed: `converters.media.*` and
`extraction.xlsx_max_rows_per_sheet` genuinely bind, through
`config.converters.model_dump()` handed to `config_from_overrides`, and a
hand-kept allowlist of those is a second thing to keep in step.

**What it misses, stated so nobody trusts it further than it goes.** A field
whose name collides with an unrelated attribute passes. Of the six removals
above it catches `slug_max_length`, `hash_length`, `write_back`,
`create_missing`, `host`, `secret_key` and `session_timeout_minutes` -- but not
`service.mode` or `web.port`, because `mode` is a keyword to `model_dump` and
`port` is an attribute of a parsed URL. Those two were found by reading. This
test is what stops the next one being added; it is not what finds them.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import BaseModel

from m365_brain.config.schema import Config

REPO_ROOT = Path(__file__).resolve().parents[2]


def _consuming_names() -> frozenset[str]:
    """Every attribute, string subscript and keyword name the source mentions."""
    import obsidian_import

    roots = [REPO_ROOT / "m365_brain", REPO_ROOT / "m365_admin", Path(obsidian_import.__file__).parent]
    names: set[str] = set()
    for root in roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    names.add(node.attr)
                elif isinstance(node, ast.keyword) and node.arg:
                    names.add(node.arg)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    names.add(node.value)
    return frozenset(names)


CONSUMED = _consuming_names()


def _declared(model: type[BaseModel], prefix: str, seen: set[type]) -> list[tuple[str, str]]:
    """`(dotted path, field name)` for every field in the tree below `model`."""
    if model in seen:
        return []
    seen = seen | {model}
    found: list[tuple[str, str]] = []
    for name, field in model.model_fields.items():
        found.append((f"{prefix}.{name}" if prefix else name, name))
        for nested in _nested_models(field.annotation):
            found.extend(_declared(nested, f"{prefix}.{name}" if prefix else name, seen))
    return found


def _nested_models(annotation: object) -> list[type[BaseModel]]:
    """Every `BaseModel` reachable from one annotation, through `|`, list and dict."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    args = getattr(annotation, "__args__", ())
    return [model for arg in args for model in _nested_models(arg)]


DECLARED = sorted(set(_declared(Config, "", set())))


@pytest.mark.parametrize("path,name", DECLARED, ids=[path for path, _ in DECLARED])
def test_every_declared_config_field_is_named_by_consuming_code(path: str, name: str) -> None:
    assert name in CONSUMED, (
        f"config field {path!r} is declared, required, and named by nothing in m365_brain/, "
        f"m365_admin/ or obsidian_import. A key an operator must spell and nobody reads is not "
        f"documentation -- it reads as a switch. Wire it up or take it out."
    )


def test_the_walk_found_the_tree_it_was_pointed_at() -> None:
    """A walk that found nothing would pass every case above vacuously."""
    assert len(DECLARED) > 100
    assert ("graph.max_pages", "max_pages") in DECLARED
    assert ("index.search.page_size", "page_size") in DECLARED
