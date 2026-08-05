"""Tests for the Extractor protocol and structural compliance of all extractor modules."""

from __future__ import annotations

import importlib
import inspect
from types import ModuleType

import pytest

from m365_brain.m365.extractors.base import Extractor, ExtractorContext

EXTRACTOR_MODULE_PATHS = [
    "m365_brain.m365.extractors.email",
    "m365_brain.m365.extractors.calendar",
    "m365_brain.m365.extractors.onedrive",
    "m365_brain.m365.extractors.sharepoint",
    "m365_brain.m365.extractors.teams_chats",
    "m365_brain.m365.extractors.teams_channels",
    "m365_brain.m365.extractors.contacts",
    "m365_brain.m365.extractors.directory",
]
"""All eight. The list held six until the uniform `run(..., ctx)` signature
landed -- and the two it omitted, contacts and directory, are precisely the
ones whose signature changed shape rather than just gaining an argument."""


@pytest.fixture(params=EXTRACTOR_MODULE_PATHS)
def extractor_module(request: pytest.FixtureRequest) -> ModuleType:
    return importlib.import_module(request.param)


def test_extractor_protocol_importable() -> None:
    """Importing the Extractor protocol does not raise."""
    assert Extractor is not None


def test_extractor_protocol_is_protocol() -> None:
    """Extractor is a typing.Protocol subclass."""
    assert issubclass(Extractor, object)
    assert getattr(Extractor, "_is_protocol", False)


def test_module_has_name_attribute(extractor_module: ModuleType) -> None:
    """Each extractor module exposes a 'name' string attribute."""
    assert hasattr(extractor_module, "name")
    assert isinstance(extractor_module.name, str)
    assert len(extractor_module.name) > 0


def test_module_has_required_scopes_attribute(extractor_module: ModuleType) -> None:
    """Each extractor module exposes a 'required_scopes' list of strings."""
    assert hasattr(extractor_module, "required_scopes")
    assert isinstance(extractor_module.required_scopes, list)
    assert all(isinstance(s, str) for s in extractor_module.required_scopes)
    assert len(extractor_module.required_scopes) > 0


def test_module_has_callable_run(extractor_module: ModuleType) -> None:
    """Each extractor module exposes a callable 'run' attribute."""
    assert hasattr(extractor_module, "run")
    assert callable(extractor_module.run)


def test_run_signature_has_required_parameters(extractor_module: ModuleType) -> None:
    """Every run takes the same five positional args — the uniform signature is the point."""
    sig = inspect.signature(extractor_module.run)
    param_names = list(sig.parameters.keys())
    required = ["client", "storage", "state", "config", "ctx"]
    assert param_names == required, f"{extractor_module.__name__}.run params {param_names} do not match {required}"


def test_run_ctx_is_an_extractor_context(extractor_module: ModuleType) -> None:
    """The fifth argument is the shared context, not a per-extractor shape."""
    hints = inspect.get_annotations(extractor_module.run, eval_str=True)
    assert hints["ctx"] is ExtractorContext, (
        f"{extractor_module.__name__}.run ctx annotation is {hints['ctx']}, expected ExtractorContext"
    )


def test_run_return_annotation(extractor_module: ModuleType) -> None:
    """The run function is annotated to return tuple[dict, int]."""
    hints = inspect.get_annotations(extractor_module.run, eval_str=True)
    assert hints["return"] == tuple[dict, int], (
        f"{extractor_module.__name__}.run return annotation is {hints['return']}, expected tuple[dict, int]"
    )
