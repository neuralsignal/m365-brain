"""No secret in the config tree survives being rendered.

The property under test is not "these four fields are redacted" -- that is a
checklist, and a checklist is one forgotten entry away from failing open. It is
"every `SecretStr` anywhere under `Config` renders masked", and the set of
`SecretStr` fields is read off the model's own annotations at test time. A fifth
secret added to any section is covered the moment it is declared, with no edit
here; `test_a_freshly_declared_secret_is_discovered` is the proof of that, and
`test_every_known_secret_is_discovered` is the guard against a walk that finds
nothing and passes vacuously.

`model_construct` builds each owner with only its secret populated. That skips
validation deliberately: the other fields are irrelevant to how a secret
renders, and requiring a valid instance of every section would put a fixture
between a new secret and its coverage -- exactly the manual step this avoids.
"""

from __future__ import annotations

import json
import logging
import typing

import pytest
from pydantic import BaseModel, SecretStr

from m365_brain.config.schema import Config

SENTINEL = "sentinel-secret-value-that-must-never-be-rendered"


def _annotation_types(annotation: object) -> list[object]:
    """Flatten an annotation to the concrete types inside it.

    `SecretStr | None`, `dict[str, AuthProfileConfig]` and a bare class all have
    to yield their members, because a secret can sit behind any of them.
    """
    args = typing.get_args(annotation)
    if not args:
        return [annotation]
    return [inner for arg in args for inner in _annotation_types(arg)]


def secret_fields(root: type[BaseModel]) -> list[tuple[type[BaseModel], str]]:
    """Every (owning model, field name) under `root` annotated `SecretStr`."""
    found: list[tuple[type[BaseModel], str]] = []
    seen: set[type[BaseModel]] = set()
    pending: list[type[BaseModel]] = [root]
    while pending:
        model = pending.pop()
        if model in seen:
            continue
        seen.add(model)
        for name, field in model.model_fields.items():
            for member in _annotation_types(field.annotation):
                if member is SecretStr:
                    found.append((model, name))
                elif isinstance(member, type) and issubclass(member, BaseModel):
                    pending.append(member)
    return sorted(found, key=lambda pair: (pair[0].__name__, pair[1]))


SECRET_FIELDS = secret_fields(Config)


def _renderings(instance: BaseModel) -> dict[str, str]:
    """Every way a value leaks in practice: an f-string, a dump, a log line."""
    logger = logging.getLogger("test_config_secrets")
    record = logger.makeRecord(
        logger.name, logging.INFO, __file__, 0, "loaded config %s (%r)", (instance, instance), None
    )
    return {
        "repr": repr(instance),
        "str": str(instance),
        "f-string": f"{instance}",
        "model_dump": str(instance.model_dump()),
        "model_dump(mode=json)": json.dumps(instance.model_dump(mode="json")),
        "model_dump_json": instance.model_dump_json(),
        "log line": logging.Formatter("%(message)s").format(record),
    }


@pytest.mark.parametrize(
    ("owner", "field_name"),
    SECRET_FIELDS,
    ids=[f"{owner.__name__}.{name}" for owner, name in SECRET_FIELDS],
)
def test_a_secret_field_renders_masked_everywhere(owner: type[BaseModel], field_name: str) -> None:
    instance = owner.model_construct(**{field_name: SecretStr(SENTINEL)})
    leaked = [surface for surface, text in _renderings(instance).items() if SENTINEL in text]
    assert not leaked, f"{owner.__name__}.{field_name} leaked through: {leaked}"


@pytest.mark.parametrize(
    ("owner", "field_name"),
    SECRET_FIELDS,
    ids=[f"{owner.__name__}.{name}" for owner, name in SECRET_FIELDS],
)
def test_a_secret_field_still_hands_out_its_value_on_request(owner: type[BaseModel], field_name: str) -> None:
    """Masking must not be the value being lost -- the unwrap is the only path in."""
    instance = owner.model_construct(**{field_name: SecretStr(SENTINEL)})
    assert getattr(instance, field_name).get_secret_value() == SENTINEL


def test_every_known_secret_is_discovered() -> None:
    """A walk that finds nothing would pass every test above vacuously.

    A subset assertion on purpose: a sixth secret must not have to be added
    here to be covered, but demoting one of these back to a plain `str` --
    which would silently drop it out of the parametrisation above -- must fail.
    """
    assert {(owner.__name__, name) for owner, name in SECRET_FIELDS} >= {
        ("AuthConfig", "client_secret"),
        ("AuthProfileConfig", "client_secret"),
        ("AzureBlobStorageConfig", "connection_string"),
        ("WebConfig", "fernet_key"),
    }


def test_a_freshly_declared_secret_is_discovered() -> None:
    """The fifth secret, added to a section that does not exist yet."""

    class Vendor(BaseModel):
        api_key: SecretStr
        endpoint: str

    class Root(BaseModel):
        vendor: Vendor | None = None
        vendors: dict[str, Vendor] | None = None

    assert secret_fields(Root) == [(Vendor, "api_key")]


def test_the_renderings_would_catch_a_leak() -> None:
    """Every surface checked above must actually be able to show a value.

    Without this, a typo that rendered nothing at all would make the leak
    assertions pass for the wrong reason.
    """

    class Leaky(BaseModel):
        key: str

    leaked = {surface for surface, text in _renderings(Leaky(key=SENTINEL)).items() if SENTINEL in text}
    assert leaked == set(_renderings(Leaky(key=SENTINEL)))


def test_a_plain_string_field_is_not_mistaken_for_a_secret() -> None:
    """The walk must key on the type, not on a name that looks secret-ish."""

    class Root(BaseModel):
        secret_key: str

    assert secret_fields(Root) == []
