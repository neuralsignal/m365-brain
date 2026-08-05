#!/usr/bin/env python3
"""Add a text layer to a scanned PDF, then read it as markdown.

A PDF that is a photograph of a page has no text to extract, so the converter
returns nothing and the failure looks like an empty document. `ocrmypdf` puts a
text layer in, and the ordinary read path takes it from there.

Two deliberate shapes:

* **No config file of its own.** Conversion happens by invoking the installed
  `m365-brain` CLI against `$M365_BRAIN_CONFIG`, so this script has no
  dependency to install, no lockfile, and no second copy of the converter
  settings to keep in step with the first.
* **No default language.** Tesseract given the wrong language does not fail --
  it produces plausible nonsense, which is far worse than a crash. The caller
  states which languages the document is in.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

CONFIG_ENV = "M365_BRAIN_CONFIG"


def ocr(source: Path, languages: str) -> Path:
    """Write a searchable copy of `source` to a temp file and return its path."""
    handle, name = tempfile.mkstemp(suffix=".pdf")
    os.close(handle)
    searchable = Path(name)
    result = subprocess.run(
        ["ocrmypdf", "--force-ocr", "-l", languages, "--output-type", "pdf", str(source), str(searchable)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        searchable.unlink(missing_ok=True)
        raise RuntimeError(f"ocrmypdf exited {result.returncode}:\n{result.stderr}")
    return searchable


def read_as_markdown(pdf: Path, config: str) -> str:
    """`m365-brain index catalog read` -- the same converter every other path uses."""
    result = subprocess.run(
        ["m365-brain", "--config", config, "index", "catalog", "read", str(pdf)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"m365-brain exited {result.returncode}:\n{result.stderr}")
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description="OCR a scanned PDF and write the text as markdown.")
    parser.add_argument("--input", required=True, help="the scanned PDF")
    parser.add_argument("--output", required=True, help="where to write the markdown")
    parser.add_argument("--languages", required=True, help="Tesseract codes, e.g. eng+deu")
    args = parser.parse_args()

    config = os.environ.get(CONFIG_ENV)
    if not config:
        print(f"{CONFIG_ENV} is not set; it must name a config file", file=sys.stderr)
        return 3

    source = Path(args.input).resolve()
    if not source.is_file():
        print(f"no such file: {source}", file=sys.stderr)
        return 1
    if source.suffix.lower() != ".pdf":
        print(f"expected a .pdf, got {source.suffix!r}", file=sys.stderr)
        return 1

    print(f"OCR: {source.name} ({args.languages})", file=sys.stderr)
    searchable = ocr(source, args.languages)
    try:
        markdown = read_as_markdown(searchable, config)
    finally:
        searchable.unlink(missing_ok=True)

    target = Path(args.output).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(markdown, encoding="utf-8")
    print(f"{len(markdown)} characters -> {target}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
