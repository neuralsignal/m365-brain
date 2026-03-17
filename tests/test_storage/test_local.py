"""Tests for local filesystem storage backend."""

from __future__ import annotations

from m365_extract.storage.local import LocalBackend


class TestLocalBackend:
    def test_write_and_read(self, tmp_path):
        backend = LocalBackend(str(tmp_path / "vault"))
        backend.write_file("test/hello.md", "# Hello")
        assert backend.read_file("test/hello.md") == "# Hello"

    def test_file_exists(self, tmp_path):
        backend = LocalBackend(str(tmp_path / "vault"))
        assert not backend.file_exists("nonexistent.md")
        backend.write_file("test.md", "content")
        assert backend.file_exists("test.md")

    def test_list_files(self, tmp_path):
        backend = LocalBackend(str(tmp_path / "vault"))
        backend.write_file("emails/a.md", "a")
        backend.write_file("emails/b.md", "b")
        backend.write_file("calendar/c.md", "c")

        email_files = backend.list_files("emails")
        assert len(email_files) == 2
        assert "emails/a.md" in email_files
        assert "emails/b.md" in email_files

    def test_list_files_empty_prefix(self, tmp_path):
        backend = LocalBackend(str(tmp_path / "vault"))
        assert backend.list_files("nonexistent") == []

    def test_delete_file(self, tmp_path):
        backend = LocalBackend(str(tmp_path / "vault"))
        backend.write_file("test.md", "content")
        assert backend.file_exists("test.md")
        backend.delete_file("test.md")
        assert not backend.file_exists("test.md")

    def test_delete_cleans_empty_dirs(self, tmp_path):
        backend = LocalBackend(str(tmp_path / "vault"))
        backend.write_file("deep/nested/file.md", "content")
        backend.delete_file("deep/nested/file.md")
        # Empty parent directories should be cleaned up
        assert not (tmp_path / "vault" / "deep" / "nested").exists()

    def test_write_creates_parent_dirs(self, tmp_path):
        backend = LocalBackend(str(tmp_path / "vault"))
        backend.write_file("a/b/c/d/file.md", "deep content")
        assert backend.read_file("a/b/c/d/file.md") == "deep content"

    def test_write_overwrites_existing(self, tmp_path):
        backend = LocalBackend(str(tmp_path / "vault"))
        backend.write_file("test.md", "v1")
        backend.write_file("test.md", "v2")
        assert backend.read_file("test.md") == "v2"

    def test_creates_base_dir(self, tmp_path):
        base = tmp_path / "new" / "vault"
        LocalBackend(str(base))
        assert base.exists()
