"""Atomic token-cache persistence, using synthetic content and mocked MSAL."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from m365_brain.config import AuthConfig
from m365_brain.m365.auth.device_code import DeviceCodeAuth
from m365_brain.m365.errors import TokenCacheError

OLD_CONTENT = '{"synthetic": "old"}'
NEW_CONTENT = '{"synthetic": "new"}'


@pytest.fixture()
def pending_cache(tmp_path: Path) -> tuple[DeviceCodeAuth, Path]:
    path = tmp_path / "cache.json"
    config = AuthConfig(
        client_id="synthetic-client",
        tenant_id="synthetic-tenant",
        scopes=["User.Read"],
        token_cache_path=str(path),
        client_secret=None,
    )
    with patch("m365_brain.m365.auth.device_code.msal.PublicClientApplication"):
        auth = DeviceCodeAuth(config, 30)
    auth._cache = MagicMock(has_state_changed=True)

    def serialize() -> str:
        # SerializableTokenCache clears this flag when serializing, before I/O.
        auth._cache.has_state_changed = False
        return NEW_CONTENT

    auth._cache.serialize.side_effect = serialize
    path.write_text(OLD_CONTENT, encoding="utf-8")
    return auth, path


@pytest.mark.parametrize("original_mode", [0o600, 0o644, 0o666])
def test_replacing_existing_cache_hardens_permissions(
    pending_cache: tuple[DeviceCodeAuth, Path], original_mode: int
) -> None:
    auth, path = pending_cache
    path.chmod(original_mode)

    auth._save_cache()

    assert path.read_text(encoding="utf-8") == NEW_CONTENT
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert auth._cache.has_state_changed is False


def test_readers_see_old_cache_until_private_flushed_file_is_replaced(
    pending_cache: tuple[DeviceCodeAuth, Path],
) -> None:
    auth, path = pending_cache
    replace, fsync = os.replace, os.fsync
    synced: list[int] = []

    def observe_fsync(fd: int) -> None:
        assert stat.S_IMODE(os.fstat(fd).st_mode) == 0o600
        synced.append(os.fstat(fd).st_ino)
        fsync(fd)

    def observe_replace(source: str | Path, destination: str | Path) -> None:
        staged = Path(source)
        assert staged.parent == path.parent
        assert path.read_text(encoding="utf-8") == OLD_CONTENT
        assert staged.read_text(encoding="utf-8") == NEW_CONTENT
        assert staged.stat().st_ino in synced
        assert stat.S_IMODE(staged.stat().st_mode) == 0o600
        assert Path(destination) == path
        replace(source, destination)

    with (
        patch("m365_brain.m365.auth.device_code.os.fsync", side_effect=observe_fsync),
        patch("m365_brain.m365.auth.device_code.os.replace", side_effect=observe_replace) as replacement,
    ):
        auth._save_cache()

    replacement.assert_called_once()
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("stage", ["serialize", "fsync", "replace"])
def test_failure_preserves_old_cache_cleans_own_temp_and_can_retry(
    pending_cache: tuple[DeviceCodeAuth, Path], stage: str
) -> None:
    auth, path = pending_cache
    untouched = path.parent / "unrelated.tmp"
    untouched.write_text("leave this file alone", encoding="utf-8")
    error = OSError("synthetic persistence failure")

    def fail_serialization() -> str:
        auth._cache.has_state_changed = False
        raise error

    target = auth._cache if stage == "serialize" else os
    name = "serialize" if stage == "serialize" else stage
    effect = fail_serialization if stage == "serialize" else error
    with patch.object(target, name, side_effect=effect), pytest.raises(TokenCacheError):
        auth._save_cache()

    assert path.read_text(encoding="utf-8") == OLD_CONTENT
    assert auth._cache.has_state_changed is True
    assert set(path.parent.iterdir()) == {path, untouched}
    assert untouched.read_text(encoding="utf-8") == "leave this file alone"

    auth._save_cache()
    assert path.read_text(encoding="utf-8") == NEW_CONTENT
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert auth._cache.has_state_changed is False
