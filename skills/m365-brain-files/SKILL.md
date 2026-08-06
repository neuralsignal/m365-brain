---
name: m365-brain-files
description: Find catalogued source files and read their contents as markdown, including OCR for scanned PDFs, and read or write a single SharePoint document through Microsoft Graph. Use when locating a document by name, converting a PDF/DOCX/XLSX to text, or updating a file in a SharePoint library.
license: MIT
compatibility: Requires the m365-brain CLI on PATH and M365_BRAIN_CONFIG set to a config file path. Document conversion needs the [convert] extra. scripts/ocr_extract.py additionally needs ocrmypdf and tesseract with the language packs you intend to use.
allowed-tools: Bash(m365-brain:*) Bash(python3:*) Read Glob
metadata:
  version: "1.0"
  category: "files"
  homepage: "https://github.com/neuralsignal/m365-brain"
---

# Files

Every command is `m365-brain --config "$M365_BRAIN_CONFIG" …`. Results go to
stdout, logs to stderr; pass `--json` when you intend to parse.

## Find a catalogued file

    m365-brain --config "$M365_BRAIN_CONFIG" index catalog list --ext .pdf --json
    m365-brain --config "$M365_BRAIN_CONFIG" index catalog list --stats --json
    m365-brain --config "$M365_BRAIN_CONFIG" index catalog search "invoice" --json
    m365-brain --config "$M365_BRAIN_CONFIG" index catalog resolve "quarterly report"

`resolve` prints one path and **refuses to guess**: a query matching several
files is an error naming them, because resolving to the first would be a coin
flip. Narrow the query.

`list` and `search` return `{"entries": [...], "total": N, "returned": M,
"limit": L}`, each entry carrying `original_path`, `extension`, `size_bytes`,
`modified_at` and `conversion_status`. **`returned < total` means rows were
withheld** — raise `--limit`, which defaults to `index.search.page_size`.
`index catalog extract` reports the same three keys against the rows it had
left to convert.

## Read a file as markdown

    m365-brain --config "$M365_BRAIN_CONFIG" index catalog read /path/to/report.docx

Converts and prints. **Writes nothing** — pipe or redirect if you want a file.
Handles PDF, DOCX, PPTX, XLSX, CSV and the other formats configured under
`converters.backends`.

If the output is empty or nearly so for a PDF, it is a scan with no text layer.
Use OCR:

    python3 scripts/ocr_extract.py --input scan.pdf --output scan.md --languages eng+deu

`--languages` is required and takes Tesseract codes. There is no default: the
wrong language silently produces plausible nonsense, which is worse than a
crash, so the caller has to say.

## One SharePoint document, both directions

    m365-brain --config "$M365_BRAIN_CONFIG" files pull \
      --profile files --site-hostname contoso.sharepoint.com --site-path /sites/Team \
      --library Documents --item-path reports/status.md --out ./status.md

    m365-brain --config "$M365_BRAIN_CONFIG" files push \
      --profile files --site-hostname contoso.sharepoint.com --site-path /sites/Team \
      --library Documents --item-path reports/status.md --in ./status.md \
      --content-type text/markdown --if-match 'W/"abc123"'

`pull` prints the eTag. `push` requires it, and there is **no unconditional
overwrite** — if the document changed since the pull, the write fails with a
412 and nothing is written. The answer is to pull again, merge, and push with
the new eTag. Never work around a 412.

## Where files come from

    m365-brain --config "$M365_BRAIN_CONFIG" index paths --json

Prints the directory each extractor writes into. Read it rather than assuming
a folder name — all of them are config.
