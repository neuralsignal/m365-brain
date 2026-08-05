"""How two spellings of the same person are decided to be the same person.

One normalizer, used by every operation that has to line a written name up with
an indexed entity. The two scripts this package absorbed each had their own,
and the two disagreed about case, about accents and about word order -- so the
same contact resolved in one report and not in the other.

Everything here is a deterministic Unicode or format transform, so nothing here
is config. `normalize_name` does not decide *whether* two names are the same
person; it decides what string the comparison runs on, and the comparison is
policy that lives in the modules above.

Word-sorting is the one non-obvious step: `"Anna Meier"` and `"Meier Anna"`
normalise to the same key, which is the whole point -- a directory export and a
hand-written note routinely disagree about which half comes first.
"""

from __future__ import annotations

import re
import unicodedata

_ADDRESS = re.compile(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;()\[\]]+")
"""An email address as it appears inside prose, angle brackets or a list.

Deliberately shape-based. Reading an address off an indexed entity otherwise
means naming the observation category that holds it, and that name is a
property of whoever wrote the corpus -- inventing one here would hardcode one
author's frontmatter vocabulary into the library.
"""


def normalize_name(value: str) -> str:
    """The comparison key for a name: accent-free, case-free, word-sorted.

    NFKD decomposition splits an accented letter into a base letter plus a
    combining mark, and dropping the marks is what makes `Müller` and `Muller`
    one key. `casefold` then does the lowering *and* the German sharp-s fold
    (`ß` -> `ss`, `Straße` == `Strasse`) in one step, which is why it is used in
    place of `lower`.

    `Müller` and `Mueller` stay **different** keys. Stripping accents and
    transliterating `ü` -> `ue` are two different conventions, and a normalizer
    that applied both would collapse pairs of genuinely distinct names -- there
    is no way to tell an author who wrote `Mueller` for `Müller` from one whose
    contact is actually called Mueller.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    unaccented = "".join(character for character in decomposed if not unicodedata.combining(character))
    return " ".join(sorted(unaccented.casefold().split()))


def deslugify(slug: str) -> str:
    """`anna-meier` -> `Anna Meier`. The inverse of whatever wrote the link."""
    return slug.replace("-", " ").title()


def reverse_comma_name(value: str) -> str:
    """`Meier, Anna` -> `Anna Meier`. A value with no comma is returned as-is.

    Applied *before* normalisation, never after: the comma binds to the word it
    follows, so word-sorting `meier, anna` yields a key with a stray comma in
    it that matches nothing.
    """
    if "," not in value:
        return value
    family, given = value.split(",", 1)
    return f"{given.strip()} {family.strip()}".strip()


def name_key(value: str) -> str:
    """The full identity key for a written name: comma form reversed, normalised.

    This is the function callers want. `normalize_name` and `reverse_comma_name`
    are exposed because they are separately meaningful, not because a caller
    should be composing them itself.
    """
    return normalize_name(reverse_comma_name(value))


def email_addresses(text: str) -> list[str]:
    """Every email address in a piece of text, casefolded, in the order found."""
    return [match.casefold() for match in _ADDRESS.findall(text)]
