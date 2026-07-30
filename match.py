"""match.py — normalisation and matching. PURE FUNCTIONS, NO I/O, 100% offline-testable.

This module is the reason the travel repo's worst bug class cannot recur here. That repo's
_location() / resolve_hotel() substring-matched free-text labels — `alias.lower() in
lookup` would match "Sony" inside "Sony Center Berlin" — and silently applied the wrong
par to every Bulgarian deal. Here NOTHING keys on prose: `name` is display-only, `sku` is
the key everywhere, and matching is whole-token AND-sets with a hard veto list.

See catalog.py's docstring for the matching rules and the scratchpad CONTRACT for the
worked token examples. `fold`, `tokens` and `slug` are the frozen seam — three modules
depend on their exact behaviour, so they are pinned here with their examples in doctest
form rather than described in prose.
"""

import re
import unicodedata

import catalog

# Bulgarian Cyrillic -> Latin. Deliberately a plain transliteration, not a
# standards-compliant one: its only job is to make one catalog rule serve Bulgarian,
# German and English sources. fold("Пушена сьомга") -> "pushena syomga", so ["syomga"],
# ["сьомга"] and ["salmon"] can all be written as rules and all hit.
_CYR_TO_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sht", "ъ": "a",
    "ь": "y", "ю": "yu", "я": "ya",
    # Russian/Serbian strays that turn up in scraped feeds
    "ы": "y", "э": "e", "ё": "e", "і": "i", "ј": "j", "ђ": "dj", "ћ": "c",
}

_PUNCT = re.compile(r"[^0-9a-zЀ-ӿ]+")


def fold(text):
    """Lowercase, strip accents, transliterate Cyrillic->Latin, punctuation->space.

    >>> fold("Пушена сьомга")
    'pushena syomga'
    >>> fold("Sony WH-1000XM5")
    'sony wh 1000xm5'
    >>> fold("Olivenöl, extra vergine")
    'olivenol extra vergine'
    """
    t = (text or "").lower()
    # NFKD + drop combining marks, so ö -> o and ä -> a before transliteration.
    t = "".join(c for c in unicodedata.normalize("NFKD", t)
                if not unicodedata.combining(c))
    t = "".join(_CYR_TO_LAT.get(c, c) for c in t)
    t = _PUNCT.sub(" ", t)
    return " ".join(t.split())


def _squash(text):
    """Lowercase and REMOVE punctuation without inserting a space.

    This is the view that recovers model numbers the folded view splits apart:
    "WH-1000XM5" folds to two tokens but squashes to one.

    >>> _squash("Sony WH-1000XM5")
    'sony wh1000xm5'
    """
    t = (text or "").lower()
    t = "".join(c for c in unicodedata.normalize("NFKD", t)
                if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^0-9a-zЀ-ӿ\s]+", "", t).split())


def tokens(text):
    """The whole-token set an offer name is matched against: a union of three views.

    A  folded   — accents stripped, Cyrillic->Latin, punctuation -> SPACE
    B  raw      — lowercased, split on whitespace, edge punctuation stripped
    C  squashed — punctuation REMOVED, not spaced

    All three exist so a catalog rule may be written in Bulgarian, German or English,
    and so a hyphenated model number is reachable as one token.

    >>> sorted(tokens("Пушена сьомга"))
    ['pushena', 'syomga', 'пушена', 'сьомга']
    >>> "wh1000xm5" in tokens("Sony WH-1000XM5")
    True
    >>> "1000xm5" in tokens("Sony WH-1000XM5")
    True
    >>> "xm5" in tokens("Sony WH-1000XM5")
    False
    """
    raw_lower = (text or "").lower()
    out = set(fold(text).split())
    out |= {t.strip(".,;:!?()[]{}\"'-/") for t in raw_lower.split()}
    out |= set(_squash(text).split())
    out.discard("")
    return out


def slug(name):
    """Stable ascii slug for a provisional off-list discovery sku.

    Used as `"disc." + slug(name)`. Stability matters: the slug IS the history key for
    that product, so a drifting name silently forks its history — which is exactly why
    `disc.*` skus prune at DISC_SKU_MAX_DAYS rather than accumulating forever.

    >>> slug("Sony WH-1000XM5 Безжични слушалки")
    'sony-wh-1000xm5-bezzhichni-slushalki'
    """
    return "-".join(fold(name).split())[:80] or "unknown"


def unit_of(sku):
    """The base unit ("kg" | "L" | "pc") a sku's par_eur is denominated in, or None."""
    return (catalog.CATALOG.get(sku) or {}).get("unit")


def to_base(value, raw_unit):
    """Convert (value, raw_unit) to (value_in_base, base_unit) via catalog.UNIT_TO_BASE.

    catalog.UNIT_TO_BASE is the ONLY place the g->kg and ml->L divisors exist. Never
    write /1000 anywhere: one duplicated divisor is how "100 g" becomes "1.09 EUR/kg",
    a fabricated 91% discount that clears every gate in the pipeline at once.

    >>> to_base(500, "g")
    (0.5, 'kg')
    >>> to_base(250, "ml")
    (0.25, 'L')
    """
    entry = catalog.UNIT_TO_BASE.get(raw_unit)
    if entry is None or value is None:
        return None, None
    base, mult = entry
    return round(value * mult, 6), base
