"""Tests for local filesystem storage backend."""

from __future__ import annotations

import pytest

from m365_extract.storage.exceptions import PathTraversalError
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

    def test_write_and_read_bytes(self, tmp_path):
        backend = LocalBackend(str(tmp_path / "vault"))
        data = b"\x89PNG\r\n\x1a\n fake binary content"
        backend.write_bytes("attachments/image.png", data)
        full = tmp_path / "vault" / "attachments" / "image.png"
        assert full.exists()
        assert full.read_bytes() == data

    def test_write_bytes_creates_parent_dirs(self, tmp_path):
        backend = LocalBackend(str(tmp_path / "vault"))
        backend.write_bytes("emails/2026/03-12/slug-abc123/attachments/doc.pdf", b"%PDF")
        assert backend.file_exists("emails/2026/03-12/slug-abc123/attachments/doc.pdf")

    def test_write_bytes_rejects_traversal(self, tmp_path):
        backend = LocalBackend(str(tmp_path / "vault"))
        with pytest.raises(PathTraversalError, match="Path traversal detected"):
            backend.write_bytes("../outside.bin", b"pwned")


class TestPathTraversalProtection:
    """Verify that all LocalBackend operations reject path traversal attempts."""

    TRAVERSAL_PATHS = [
        "../../etc/passwd",
        "../outside.md",
        "emails/../../../etc/cron.d/malicious",
        "/etc/passwd",
    ]

    def test_write_file_rejects_traversal(self, tmp_path):
        backend = LocalBackend(str(tmp_path / "vault"))
        for malicious_path in self.TRAVERSAL_PATHS:
            with pytest.raises(PathTraversalError, match="Path traversal detected"):
                backend.write_file(malicious_path, "pwned")

    def test_read_file_rejects_traversal(self, tmp_path):
        backend = LocalBackend(str(tmp_path / "vault"))
        for malicious_path in self.TRAVERSAL_PATHS:
            with pytest.raises(PathTraversalError, match="Path traversal detected"):
                backend.read_file(malicious_path)

    def test_file_exists_rejects_traversal(self, tmp_path):
        backend = LocalBackend(str(tmp_path / "vault"))
        for malicious_path in self.TRAVERSAL_PATHS:
            with pytest.raises(PathTraversalError, match="Path traversal detected"):
                backend.file_exists(malicious_path)

    def test_list_files_rejects_traversal(self, tmp_path):
        backend = LocalBackend(str(tmp_path / "vault"))
        for malicious_path in self.TRAVERSAL_PATHS:
            with pytest.raises(PathTraversalError, match="Path traversal detected"):
                backend.list_files(malicious_path)

    def test_delete_file_rejects_traversal(self, tmp_path):
        backend = LocalBackend(str(tmp_path / "vault"))
        for malicious_path in self.TRAVERSAL_PATHS:
            with pytest.raises(PathTraversalError, match="Path traversal detected"):
                backend.delete_file(malicious_path)

    def test_traversal_does_not_write_outside_base(self, tmp_path):
        """Ensure no file is created outside the vault even on attempted traversal."""
        backend = LocalBackend(str(tmp_path / "vault"))
        target = tmp_path / "outside.md"
        with pytest.raises(PathTraversalError):
            backend.write_file("../outside.md", "pwned")
        assert not target.exists()

    def test_legitimate_nested_paths_still_work(self, tmp_path):
        """Paths that look suspicious but resolve within base should work."""
        backend = LocalBackend(str(tmp_path / "vault"))
        backend.write_file("emails/2026/03/subject.md", "content")
        assert backend.read_file("emails/2026/03/subject.md") == "content"
