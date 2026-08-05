"""Markdown to the HTML Outlook actually renders, and body composition.

Outlook strips a `<style>` block out of a message body, so every block element
has to carry its own inline style or the draft arrives as unstyled runs of
text. That is why this is a list of string substitutions rather than a CSS
sheet, why several properties appear twice in different cases (`margin` and
`Margin` -- Outlook honours the capitalised one), and why `<li>` carries
`mso-special-format:bullet`. None of it is decoration; a port that emitted a
bare markdown fragment would produce drafts that look broken and no test would
notice.

Kept as a table of literal replacements, in order, exactly as the working
implementation had it. Rewriting it into a "cleaner" HTML post-processor is how
one of these silently stops matching.
"""

from __future__ import annotations

import markdown as markdown_lib

MARKDOWN_EXTENSIONS = ["tables"]

OUTLOOK_STYLES: tuple[tuple[str, str], ...] = (
    ("<table>", '<table style="border-collapse:collapse;font-family:Calibri,Arial,sans-serif;font-size:11pt">'),
    ("<th>", '<th style="border:1px solid #ccc;padding:4px 8px;background:#f5f5f5;text-align:left">'),
    ("<td>", '<td style="border:1px solid #ccc;padding:4px 8px">'),
    ("<ul>", '<ul style="margin:10px 0 10px 25px;Margin:10px 0 10px 25px;padding:0;">'),
    ("<ol>", '<ol style="margin:10px 0 10px 25px;Margin:10px 0 10px 25px;padding:0;list-style-type:decimal;">'),
    ("<li>", '<li style="margin:0 0 4px 0;Margin:0 0 4px 0;mso-special-format:bullet;">'),
    ("<h1>", '<h1 style="font-size:20px;font-weight:bold;margin:16px 0 8px 0;Margin:16px 0 8px 0;">'),
    ("<h2>", '<h2 style="font-size:16px;font-weight:bold;margin:14px 0 6px 0;Margin:14px 0 6px 0;">'),
    ("<h3>", '<h3 style="font-size:13px;font-weight:bold;margin:12px 0 4px 0;Margin:12px 0 4px 0;">'),
    (
        "<blockquote>",
        '<blockquote style="margin:10px 0 10px 20px;Margin:10px 0 10px 20px;padding:8px 12px;'
        'border-left:3px solid #cccccc;color:#555555;">',
    ),
    ("<hr />", '<hr style="border:none;border-top:1px solid #cccccc;margin:16px 0;Margin:16px 0;" />'),
    ("<hr>", '<hr style="border:none;border-top:1px solid #cccccc;margin:16px 0;Margin:16px 0;">'),
    (
        "<pre>",
        '<pre style="margin:10px 0;Margin:10px 0;padding:10px 12px;background-color:#f5f5f5;'
        'font-family:Consolas,Courier New,monospace;font-size:10pt;border:1px solid #e0e0e0;">',
    ),
    (
        "<code>",
        '<code style="font-family:Consolas,Courier New,monospace;font-size:10pt;'
        'background-color:#f5f5f5;padding:1px 4px;">',
    ),
)

BODY_SEPARATOR = "<br><br>"


def markdown_to_outlook_html(markdown_text: str) -> str:
    """Render markdown, then inline every block style Outlook needs."""
    html = markdown_lib.markdown(markdown_text, extensions=MARKDOWN_EXTENSIONS)
    for plain, styled in OUTLOOK_STYLES:
        html = html.replace(plain, styled)
    return html


def compose_with_signature(body_html: str, signature_html: str) -> str:
    """Append the signature, or return the body untouched when there is none.

    An empty signature is the `include_signature: false` case and must also
    suppress the signature's inline logo -- see `email.py`. Appending an empty
    string would leave a dangling separator and a `cid:` reference with nothing
    behind it.
    """
    if signature_html:
        return f"{body_html}{BODY_SEPARATOR}{signature_html}"
    return body_html


def merge_reply_body(original_body_html: str, user_body_html: str, signature_html: str) -> str:
    """`user body + signature + Graph's own quoted original`, in that order.

    The quoted original comes from Graph's `createReply` stub, which is why the
    reply flow reads the draft back before patching it. Composing without it
    loses the quote entirely and the recipient sees a reply with no thread.
    """
    return f"{compose_with_signature(user_body_html, signature_html)}{BODY_SEPARATOR}{original_body_html}"
