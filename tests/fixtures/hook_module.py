"""A real importable module for the hook resolver to resolve against.

Resolution imports by dotted path, so a `MagicMock` proves nothing here: the
test has to hand `importlib` something it can actually find.
"""

from __future__ import annotations

seen: list[str] = []
"""Cycle ids the hooks below were called with, in order. Cleared per test."""


def on_cycle(manifest) -> None:
    seen.append(f"on_cycle:{manifest.cycle_id}")


def also_on_cycle(manifest) -> None:
    seen.append(f"also_on_cycle:{manifest.cycle_id}")


def explodes(manifest) -> None:
    seen.append(f"explodes:{manifest.cycle_id}")
    raise RuntimeError("hook blew up")


def explodes_silently(manifest) -> None:
    raise RuntimeError


def takes_nothing() -> None:
    """Wrong arity: no manifest to receive."""


def takes_two(manifest, extra) -> None:
    """Wrong arity: nothing supplies the second argument."""


def takes_one_and_an_optional(manifest, verbose=False) -> None:
    """Legal: the optional one is the hook author's business."""
    seen.append(f"optional:{manifest.cycle_id}")


def takes_varargs(*args) -> None:
    """Legal: `*args` accepts the manifest."""
    seen.append(f"varargs:{args[0].cycle_id}")


not_callable = "this is a string"
