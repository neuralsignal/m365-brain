"""The extension point: `module.path:callable`, one argument, run after a cycle.

Resolution happens in two phases, and the split is deliberate. `load_config()`
checks the *shape* of a spec string and stops there, because a Pydantic
validator that imports arbitrary third-party modules turns `config show` into
arbitrary code execution. `resolve_hooks()` does the importing, and it runs at
startup -- before the first cycle, before any extractor touches Graph -- so a
typo'd hook path fails in under a second rather than four hours into a
SharePoint pass. `config validate` calls it too, which is what makes that verb
a real preflight instead of a YAML syntax check.

**Two broad `except Exception` blocks exist in this package.** This module has
one of them, and it is load-bearing: a hook is code this library has never
seen, running in this library's process, and there is no exception type it can
usefully enumerate. The other is the continuous loop in `cycle.py`.

Catching is not swallowing. A raising hook is logged with its full traceback,
recorded on the manifest, and persisted -- and it makes `manifest.ok` false, so
`run --once` exits non-zero and `status` keeps reporting it. The remaining
hooks still fire, and nothing about the outcome claims success. That is the
whole of "fail-soft".

**There is no timeout.** A thread-based one cannot actually stop a blocked
callable -- it produces a lying log line and a leaked thread. If hook isolation
ever matters the upgrade is a subprocess pool; today the consuming environment
supplies the callable and owns its behaviour.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import structlog

from m365_brain.manifest import ChangeManifest, HookOutcome

log = structlog.get_logger()

ACCEPTS_POSITIONAL = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)


class HookResolutionError(Exception):
    """A configured hook cannot be imported, found, or called with a manifest."""


@dataclass(frozen=True)
class ResolvedHook:
    """A spec that has been imported and checked. Ready to call."""

    spec: str
    call: Callable[[ChangeManifest], None]


def resolve_hooks(specs: Sequence[str]) -> list[ResolvedHook]:
    """Import every spec, in config order. Raises on the first that fails.

    Raising rather than skipping: a hook the operator configured and this
    process cannot find is a broken deployment, and starting anyway would run
    cycles that quietly do less than the config says they do.
    """
    return [ResolvedHook(spec=spec, call=_resolve_one(spec)) for spec in specs]


def dispatch(hooks: Sequence[ResolvedHook], manifest: ChangeManifest) -> list[HookOutcome]:
    """Call every hook in order. One outcome each, failures included."""
    outcomes: list[HookOutcome] = []
    for hook in hooks:
        try:
            hook.call(manifest)
        except Exception as exc:  # noqa: BLE001 -- see the module docstring
            log.exception("hook.failed", spec=hook.spec, cycle_id=manifest.cycle_id)
            outcomes.append(HookOutcome(spec=hook.spec, error=str(exc) or type(exc).__name__))
        else:
            log.debug("hook.ok", spec=hook.spec, cycle_id=manifest.cycle_id)
            outcomes.append(HookOutcome(spec=hook.spec, error=None))
    return outcomes


def _resolve_one(spec: str) -> Callable[[ChangeManifest], None]:
    module_path, _, attribute = spec.partition(":")
    if not attribute:
        raise HookResolutionError(f"hook {spec!r} is not 'module.path:callable' -- the colon is required")

    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise HookResolutionError(f"hook {spec!r}: cannot import module {module_path!r} -- {exc}") from exc

    try:
        target = getattr(module, attribute)
    except AttributeError as exc:
        raise HookResolutionError(f"hook {spec!r}: module {module_path!r} has no attribute {attribute!r}") from exc

    if not callable(target):
        raise HookResolutionError(f"hook {spec!r}: {attribute!r} is not callable (it is a {type(target).__name__})")

    _check_arity(spec, target)
    return target


def _check_arity(spec: str, target: Callable[..., object]) -> None:
    """One positional parameter, no more required. Checked before anything runs.

    A builtin or C callable has no introspectable signature; that is not a
    reason to refuse it, so an unreadable signature passes and any mismatch
    surfaces as a hook failure on the first cycle instead.
    """
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):
        return

    positional = [p for p in signature.parameters.values() if p.kind in ACCEPTS_POSITIONAL]
    variadic = any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in signature.parameters.values())
    if variadic and not positional:
        return
    required = [p for p in positional if p.default is inspect.Parameter.empty]
    if len(positional) < 1 or len(required) > 1:
        raise HookResolutionError(
            f"hook {spec!r}: must accept exactly one positional argument (the manifest), "
            f"but its signature is {signature}"
        )
