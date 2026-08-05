# Parity assets

Synthetic files the parity fixtures attach. Committed so the recording (run
against the previous implementation) and the replay (run against this one)
read identical bytes and therefore produce identical `contentBytes`.

`large.bin` is **not** committed: it has to exceed the previous
implementation's hardcoded 2.25 MB inline-attachment ceiling to reach the
upload-session path, and a 2.3 MB file of zeros in git buys nothing. Both the
recorder and the parity test generate it from
`tests/unit/m365/outboxes/test_parity.py::LARGE_ATTACHMENT_BYTES`.

## The one normalisation applied at record time

The signature logo's `contentId` was a module constant in the implementation
these fixtures were recorded from, and its value was one tenant's brand token.
It is now `outboxes.email.signature.logo_content_id`, so the recorded value was
rewritten to `brand_logo` — the value the test config sets and the value the
synthetic signature HTML references.

That is the whole of it. Everything else in these files is verbatim: method,
URL, ordering, headers, and body bytes.
