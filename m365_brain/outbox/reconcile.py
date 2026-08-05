"""What happened to a dispatched draft: sent, amended, rejected, still pending.

`classify` is **pure** over `(receipt, item, original_body, markers)`. It makes
no Graph call and touches no filesystem, which is what makes an offline replay
of a whole corpus possible -- and a differential replay against the
implementation this ports is the only evidence that the port preserved a
heuristic nobody can eyeball.

Two things left the library deliberately. The quote-marker table is **config**:
its entries are locale-specific and one of them is a person's own sign-off
phrase, so compiling it into a package would ship one user's habits to
everybody. And everything that files an outcome into a knowledge base -- sent
records, rejection registries, manual-reply detection over folder names -- is
the consumer's, because every one of those is a convention of the host rather
than a fact about the message. `ReconcileOutcome` carries the sent HTML and the
original body **by value** so no path ever crosses the boundary in either
direction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from m365_brain.vault.dispatch import DispatchReceipt

RECONCILE_SELECT = ["id", "isDraft", "subject", "body", "conversationId", "sentDateTime"]
"""The `$select` reconciliation needs. Dropping a field here does not fail --
it degrades classification silently, which is why the list is named."""

Verdict = Literal["sent", "amended", "rejected", "pending"]

TERMINAL_VERDICTS: frozenset[str] = frozenset({"sent", "amended", "rejected"})


@dataclass(frozen=True)
class QuoteMarkers:
    """Where the user's own text stops and the quoted original begins."""

    patterns: tuple[re.Pattern[str], ...]

    @classmethod
    def from_config(cls, patterns: list[str]) -> QuoteMarkers:
        """Compile `outboxes.reconcile.quote_markers`. A bad regex crashes at
        load, naming the pattern -- not on the first reply of the day."""
        compiled = []
        for pattern in patterns:
            try:
                compiled.append(re.compile(pattern, re.MULTILINE | re.IGNORECASE))
            except re.error as exc:
                raise ValueError(f"outboxes.reconcile.quote_markers entry {pattern!r} is not a regex: {exc}") from exc
        return cls(tuple(compiled))


class ReconcileOutcome(BaseModel):
    """One draft's fate, complete enough that a hook needs nothing else."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    uuid: str
    verdict: Verdict
    graph_message_id: str
    conversation_id: str
    sent_at: str
    sent_body_html: str
    original_body: str


def normalize_whitespace(text: str) -> str:
    """Collapse every run of whitespace to one space, and strip."""
    return re.sub(r"\s+", " ", text).strip()


def html_to_text(html: str) -> str:
    """Flatten an HTML document or fragment to plain text, one line per block."""
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "html.parser").get_text(separator="\n")


def markdown_to_text(markdown_text: str) -> str:
    """Flatten markdown to plain text through its rendered HTML.

    Via HTML rather than by stripping syntax, so the comparison sees what the
    recipient saw: a table renders to its cell text either way.
    """
    import markdown as markdown_lib

    return html_to_text(markdown_lib.markdown(markdown_text, extensions=["tables"]))


def user_portion(text: str, markers: QuoteMarkers) -> str:
    """Everything before the earliest quote or signature marker.

    A reply is `what the user wrote` followed by `the quoted original`, and
    comparing the whole thing would call every reply amended.
    """
    cut = len(text)
    for pattern in markers.patterns:
        match = pattern.search(text)
        if match and match.start() < cut:
            cut = match.start()
    return text[:cut]


def detect_amended(original_markdown: str, sent_html: str, markers: QuoteMarkers) -> bool:
    """Did the user materially edit the draft before sending it?

    Deliberately coarse: a downstream reviewer does the semantic diff, and this
    boolean only has to be a reasonable flag. Containment counts as unamended
    because appending a line to an otherwise-untouched draft is the common
    case and is not what "the AI got it wrong" means.
    """
    original = normalize_whitespace(markdown_to_text(original_markdown))
    sent = normalize_whitespace(user_portion(html_to_text(sent_html), markers))

    if not original and not sent:
        return False
    if original == sent:
        return False
    # Containment is not an amendment: adding a line to an otherwise-untouched
    # draft leaves the model's own text intact, which is what this measures.
    return not (original and original in sent)


def classify(
    receipt: DispatchReceipt,
    item: dict | None,
    original_body: str,
    markers: QuoteMarkers,
) -> ReconcileOutcome:
    """Turn a Graph message (or its absence) into a verdict. Pure.

    `amended` is a fourth verdict rather than a boolean on `sent`. The counters
    this replaces treated it as a subset and double-counted every amended
    draft; a caller that wants the old shape maps `amended -> (sent, amended)`
    explicitly, which is at least visible.
    """
    if item is None:
        # Graph 404: the user deleted the draft. A rejection, and the only
        # signal a rejection ever produces.
        return _outcome(receipt, "rejected", {}, original_body)
    if item.get("isDraft") is False:
        sent_html = item.get("body", {}).get("content", "")
        verdict: Verdict = "amended" if detect_amended(original_body, sent_html, markers) else "sent"
        return _outcome(receipt, verdict, item, original_body)
    return _outcome(receipt, "pending", item, original_body)


def _outcome(receipt: DispatchReceipt, verdict: Verdict, item: dict, original_body: str) -> ReconcileOutcome:
    return ReconcileOutcome(
        uuid=receipt.uuid,
        verdict=verdict,
        graph_message_id=receipt.graph_message_id or "",
        conversation_id=str(item.get("conversationId", "") or ""),
        sent_at=str(item.get("sentDateTime", "") or ""),
        sent_body_html=str(item.get("body", {}).get("content", "") or ""),
        original_body=original_body,
    )
