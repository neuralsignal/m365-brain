"""Hook resolution and dispatch, including exactly what fail-soft means."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from m365_brain.config import HooksConfig, is_hook_spec
from m365_brain.hooks import HookResolutionError, ResolvedHook, dispatch, resolve_hooks
from m365_brain.manifest import ChangeManifest
from tests.fixtures import hook_module

MODULE = "tests.fixtures.hook_module"
NOW = datetime(2026, 8, 5, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def clean_module():
    hook_module.seen.clear()
    yield
    hook_module.seen.clear()


@pytest.fixture()
def manifest() -> ChangeManifest:
    return ChangeManifest(
        cycle_id="20260805T120000Z-aaaaaa",
        started_at=NOW,
        finished_at=NOW,
        extractors=[],
        index=None,
        hooks=[],
    )


class TestResolution:
    def test_resolves_a_real_callable(self):
        hooks = resolve_hooks([f"{MODULE}:on_cycle"])
        assert hooks == [ResolvedHook(spec=f"{MODULE}:on_cycle", call=hook_module.on_cycle)]

    def test_resolves_nothing_from_an_empty_list(self):
        assert resolve_hooks([]) == []

    def test_preserves_config_order(self):
        specs = [f"{MODULE}:also_on_cycle", f"{MODULE}:on_cycle"]
        assert [hook.spec for hook in resolve_hooks(specs)] == specs

    def test_an_unknown_module_raises_naming_the_spec(self):
        with pytest.raises(HookResolutionError, match="cannot import module"):
            resolve_hooks(["no_such_package.hooks:on_cycle"])

    def test_an_unknown_attribute_raises_naming_the_attribute(self):
        with pytest.raises(HookResolutionError, match="no attribute 'nope'"):
            resolve_hooks([f"{MODULE}:nope"])

    def test_a_non_callable_raises(self):
        with pytest.raises(HookResolutionError, match="is not callable"):
            resolve_hooks([f"{MODULE}:not_callable"])

    def test_no_colon_raises(self):
        with pytest.raises(HookResolutionError, match="the colon is required"):
            resolve_hooks([f"{MODULE}.on_cycle"])

    @pytest.mark.parametrize("attribute", ["takes_nothing", "takes_two"])
    def test_wrong_arity_raises(self, attribute):
        with pytest.raises(HookResolutionError, match="exactly one positional argument"):
            resolve_hooks([f"{MODULE}:{attribute}"])

    @pytest.mark.parametrize("attribute", ["takes_one_and_an_optional", "takes_varargs"])
    def test_a_workable_signature_is_accepted(self, attribute):
        assert len(resolve_hooks([f"{MODULE}:{attribute}"])) == 1

    def test_an_uninspectable_callable_is_allowed_through(self):
        """A C builtin has no signature; refusing it would be a guess."""
        assert len(resolve_hooks(["builtins:print"])) == 1

    def test_the_first_bad_spec_stops_resolution(self):
        with pytest.raises(HookResolutionError):
            resolve_hooks([f"{MODULE}:on_cycle", "no_such_package:x", f"{MODULE}:also_on_cycle"])

    def test_resolution_does_not_call_anything(self):
        resolve_hooks([f"{MODULE}:explodes"])
        assert hook_module.seen == []


class TestConfigShapeCheck:
    """Config validates the shape; it must not import."""

    @pytest.mark.parametrize("spec", ["pkg:fn", "pkg.sub.mod:fn", "_p:_f"])
    def test_accepts_a_well_shaped_spec(self, spec):
        assert is_hook_spec(spec)

    @pytest.mark.parametrize("spec", ["pkg.fn", "pkg:", ":fn", "pkg:fn:extra", "pkg fn", ""])
    def test_rejects_a_malformed_spec(self, spec):
        assert not is_hook_spec(spec)

    def test_a_shape_check_passes_for_a_module_that_does_not_exist(self):
        """Parsing must stay pure -- no import, so no ImportError here."""
        HooksConfig(post_cycle=["no_such_package.at_all:fn"], post_reconcile=[])

    def test_a_malformed_spec_fails_at_parse(self):
        with pytest.raises(ValueError, match="module.path:callable"):
            HooksConfig(post_cycle=["not-a-spec"], post_reconcile=[])


class TestDispatch:
    def test_calls_the_hook_with_the_manifest(self, manifest):
        dispatch(resolve_hooks([f"{MODULE}:on_cycle"]), manifest)
        assert hook_module.seen == [f"on_cycle:{manifest.cycle_id}"]

    def test_runs_in_config_order(self, manifest):
        specs = [f"{MODULE}:also_on_cycle", f"{MODULE}:on_cycle"]
        dispatch(resolve_hooks(specs), manifest)
        assert hook_module.seen == [
            f"also_on_cycle:{manifest.cycle_id}",
            f"on_cycle:{manifest.cycle_id}",
        ]

    def test_a_success_records_no_error(self, manifest):
        outcomes = dispatch(resolve_hooks([f"{MODULE}:on_cycle"]), manifest)
        assert [(o.spec, o.error) for o in outcomes] == [(f"{MODULE}:on_cycle", None)]

    def test_a_raising_hook_is_recorded_not_reraised(self, manifest):
        outcomes = dispatch(resolve_hooks([f"{MODULE}:explodes"]), manifest)
        assert outcomes[0].error == "hook blew up"

    def test_a_raising_hook_does_not_stop_the_next_one(self, manifest):
        specs = [f"{MODULE}:explodes", f"{MODULE}:on_cycle"]
        outcomes = dispatch(resolve_hooks(specs), manifest)
        assert [o.error is None for o in outcomes] == [False, True]
        assert f"on_cycle:{manifest.cycle_id}" in hook_module.seen

    def test_an_exception_with_no_message_still_reports_something(self, manifest):
        outcomes = dispatch(resolve_hooks([f"{MODULE}:explodes_silently"]), manifest)
        assert outcomes[0].error == "RuntimeError"

    def test_the_traceback_reaches_the_log(self, manifest, capsys):
        """`log.exception`, not a one-line summary -- an operator needs the frames."""
        dispatch(resolve_hooks([f"{MODULE}:explodes"]), manifest)
        captured = capsys.readouterr()
        # Both streams: whether structlog is configured at all depends on
        # whether another test in the session called `configure_logging`, and
        # the unconfigured default is stdout.
        rendered = captured.out + captured.err
        assert "hook.failed" in rendered
        assert "hook blew up" in rendered
        assert "Traceback" in rendered

    def test_a_hook_failure_degrades_the_cycle_verdict(self, manifest):
        """Fail-soft, not swallowed: the cycle finishes and reports failure."""
        outcomes = dispatch(resolve_hooks([f"{MODULE}:explodes"]), manifest)
        assert not manifest.model_copy(update={"hooks": outcomes}).ok

    def test_no_hooks_means_no_outcomes(self, manifest):
        assert dispatch([], manifest) == []
