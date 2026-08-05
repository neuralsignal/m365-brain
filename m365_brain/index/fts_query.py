"""Turn what a person typed into an expression FTS5 will accept.

Five separate defects used to reach SQLite's parser and abort a search
mid-query rather than return nothing:

  * a hyphen made FTS5 read the rest as a column filter, so `well-known`
    answered "no such column: known" -- and so did every product name
  * `-term` was negated only after `(`, `AND`, `OR` or at the start, so the
    ordinary `a -b` emitted `-b*`, whose leading `-` the tokenizer strips:
    the exact opposite result set, with no error
  * an unterminated quote left an odd quote count, which FTS5 rejects
  * a stray `)` and a bare `OR` were each a syntax error
  * a NUL byte reads to SQLite as a string terminator

The property that catches all of them is not "quotes are balanced" or "one
star per word" -- those all passed. It is `test_output_is_valid_fts5`: run
the output through a real FTS5 parser and require it to be accepted.

The repair philosophy is asymmetric on purpose. A slip whose intent is
obvious is repaired (an unbalanced bracket, a dangling conjunction, a
control character); a construct FTS5 genuinely cannot express is rejected
loudly (a unary NOT). Repairing a negation would silently return the
complement of what was asked, which is worse than an error.
"""

from __future__ import annotations

import unicodedata

_OPERATORS = frozenset({"AND", "OR", "NOT"})
_BREAKS = '()"'


def to_fts_query(text: str) -> str:
    """Normalize free text into an FTS5 expression.

    Terms are ANDed, not ORed: someone typing two words wants documents with
    both, and OR-by-default makes the second word dilute the first. Bare terms
    gain a `*` so a search for `proj` finds `project`; quoted phrases and
    parentheses pass through untouched, and a leading `-` becomes `NOT`.

    `and`, `or` and `not` are read as operators whatever their case. That does
    mean a sentence containing the word "and" loses it, which is the price of
    not requiring anyone to shout; a phrase in quotes keeps every word.
    """
    text = _without_control_characters(text).strip()
    if not text:
        return ""
    tokens = _drop_dangling_operators(_balanced(_tokenize(text)))
    if not tokens:
        return ""
    tokens = _with_implicit_and(tokens)
    _reject_unary_not(tokens, text)
    return " ".join(tokens)


def _without_control_characters(text: str) -> str:
    """Drop C0/C1 control characters, keeping ordinary whitespace.

    A NUL is the one that matters: SQLite reads it as a string terminator and
    reports "unterminated string" for an expression that looks fine in Python.
    None of these carry search meaning, so dropping beats rejecting -- text
    pasted out of a PDF or a terminal routinely carries a stray one.
    """
    return "".join(
        character for character in text if character.isspace() or not unicodedata.category(character).startswith("C")
    )


def _drop_dangling_operators(tokens: list[str]) -> list[str]:
    """Drop `AND`/`OR` that has no term on one side.

    FTS5 rejects `cats OR`, `OR cats` and a bare `OR` outright, so each used to
    abort the search. Typing a trailing conjunction is an ordinary slip -- the
    useful reading is the terms around it -- and someone who types just `or`
    gets an empty query rather than an error about a word they did meant.

    `NOT` is deliberately excluded: it is handled by `_reject_unary_not`,
    because dropping it would silently return the *opposite* result set rather
    than a narrower one.
    """
    kept = list(tokens)
    changed = True
    while changed:
        changed = False
        for position, token in enumerate(kept):
            if token not in ("AND", "OR"):
                continue
            before = kept[position - 1] if position else None
            after = kept[position + 1] if position + 1 < len(kept) else None
            orphaned = (
                before is None or after is None or before in ("AND", "OR", "NOT", "(") or after in ("AND", "OR", ")")
            )
            if orphaned:
                del kept[position]
                changed = True
                break
    return kept


def _balanced(tokens: list[str]) -> list[str]:
    """Drop unmatched `)`, close unmatched `(`, and remove empty groups.

    A stray bracket is a syntax error in FTS5, so a query that is nothing but
    `)` used to abort the search. Typing an unbalanced bracket is an ordinary
    slip, and the useful reading of it is "they meant the terms", not "fail".
    Empty groups go too: `( )` is a syntax error in its own right, and an
    unbalanced bracket often leaves one behind once the partner is dropped.
    """
    kept: list[str] = []
    depth = 0
    for token in tokens:
        if token == ")":
            if depth == 0:
                continue  # nothing open -- the bracket is noise
            depth -= 1
        elif token == "(":
            depth += 1
        kept.append(token)
    kept.extend(")" * depth)

    while True:
        for position in range(len(kept) - 1):
            if kept[position] == "(" and kept[position + 1] == ")":
                del kept[position : position + 2]
                break
        else:
            return kept


def _reject_unary_not(tokens: list[str], text: str) -> None:
    """FTS5's NOT is binary; every one of them needs a left operand.

    `NOT java*`, `a* OR NOT b*` and `( NOT b* )` are all syntax errors rather
    than "everything except b" -- FTS5 simply cannot express a complemented
    set. Saying so here beats letting the driver report a syntax error against
    a query string the user never typed and cannot read.

    `AND NOT` does not reach this check: it means the same as `NOT` and is
    rewritten on the way in.
    """
    for position, token in enumerate(tokens):
        if token != "NOT":
            continue
        previous = tokens[position - 1] if position else None
        if previous is None or previous in ("AND", "OR", "NOT", "("):
            raise ValueError(
                f"negation needs something to subtract from: {text!r} "
                f"(FTS5 has no unary NOT -- try `something {text.strip()}`)"
            )


# -- internals ------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    position = 0
    while position < len(text):
        character = text[position]
        if character == '"':
            # An unterminated quote is closed rather than passed through. FTS5
            # rejects an odd quote count outright, so leaving it open turns a
            # half-typed phrase into a crash instead of a search.
            end = text.find('"', position + 1)
            if end == -1:
                tokens.append(text[position:] + '"')
                position = len(text)
            else:
                tokens.append(text[position : end + 1])
                position = end + 1
        elif character in "()":
            tokens.append(character)
            position += 1
        elif character.isspace():
            position += 1
        elif character == "-":
            # A hyphen only starts a token after whitespace, an operator or a
            # bracket -- inside a word it is consumed by `_take_word`. So a
            # leading one is always a negation, including after a plain term:
            # `python -java` is the ordinary way people write it, and the
            # version that only negated after AND/OR/( turned it into a bare
            # `-java*`, which the tokenizer then reads as `java*` -- the exact
            # opposite of what was asked, silently.
            position = _take_negated_word(text, position + 1, tokens)
        else:
            position = _take_word(text, position, tokens)
    return tokens


def _take_negated_word(text: str, start: int, tokens: list[str]) -> int:
    """Emit `NOT word*`, absorbing a preceding `AND`.

    FTS5's `NOT` is a *binary* operator, so `a AND NOT b` is a syntax error
    where `a NOT b` is the same intent spelled correctly. A user writing
    `python AND -java` means the latter, so the explicit `AND` is dropped
    rather than passed through to fail.
    """
    end = _word_end(text, start)
    if end > start:
        if tokens and tokens[-1] == "AND":
            tokens.pop()
        tokens.append("NOT")
        tokens.append(_as_term(text[start:end]))
    return end


def _as_term(word: str) -> str:
    """Render one word as an FTS5 prefix term, quoting it if it needs quoting.

    An FTS5 bareword is alphanumerics and underscores. Anything else -- the
    hyphen in `well-known`, the dot and at-sign in an email address -- makes
    the parser read the rest as a column filter and fail with "no such column",
    aborting the whole search. Quoting turns the word into a phrase, which
    tokenizes to the same terms and still accepts a trailing `*`.
    """
    if word.replace("_", "").isalnum():
        return word + "*"
    return f'"{word}"*'


def _take_word(text: str, start: int, tokens: list[str]) -> int:
    end = _word_end(text, start)
    word = text[start:end]
    tokens.append(word.upper() if word.upper() in _OPERATORS else _as_term(word))
    return end


def _word_end(text: str, start: int) -> int:
    end = start
    while end < len(text) and not text[end].isspace() and text[end] not in _BREAKS:
        end += 1
    return end


def _with_implicit_and(tokens: list[str]) -> list[str]:
    """Insert `AND` between two adjacent terms that carry no operator."""
    result: list[str] = []
    for token in tokens:
        if result and token not in ("AND", "OR", "NOT", ")") and result[-1] not in ("AND", "OR", "NOT", "("):
            result.append("AND")
        result.append(token)
    return result
