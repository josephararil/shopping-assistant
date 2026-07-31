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


def _fold_accents(text):
    """Lowercase + strip diacritics ONLY — no Cyrillic transliteration.

    catalog.UNIT_ALIASES deliberately keeps Cyrillic spellings ("бр", "кг") alongside
    Latin ones, matched against "the lowercased, accent-folded token" per its docstring.
    Using fold() here (which transliterates Cyrillic->Latin) would turn "бр" into "br",
    which is not a key in UNIT_ALIASES, so unit parsing needs this lighter pass instead.
    """
    t = (text or "").lower()
    return "".join(c for c in unicodedata.normalize("NFKD", t)
                    if not unicodedata.combining(c))


# Longest alias first so a prefix (e.g. "г") never shadows a longer alias (e.g. "гр")
# at the regex-alternation level; \b would usually save us anyway since Cyrillic
# letters count as \w, but sorting keeps the intent explicit.
_UNIT_ALT = "|".join(re.escape(k) for k in
                      sorted(catalog.UNIT_ALIASES, key=len, reverse=True))
# The `(?<![\d][-/])` lookbehind is a CALIBRE GUARD. Bulgarian grocery names carry
# grading notation — "Боб насипен 200-220/100 г" means 200-220 beans per 100 g, a
# quality grade on a loose-weight product sold by the kilo. Without the guard that reads
# as a 100 g pack, so a €1.69/kg bag of beans becomes €16.90/kg: a 10x overstatement that
# gets the best find of the week rejected as over-par before any LLM sees it. Rejecting a
# number preceded by digit-then-hyphen-or-slash kills the grading form and nothing else —
# "2x500 g" is preceded by "x", "3,5% 1 л" by a space. Lidl rows now carry an authoritative
# net_qty (see annotate), but ccc / mydealz / llm_discover names never will, so the guard
# stays load-bearing for them.
_QTY_RE = re.compile(
    r'\b(?:(\d+)\s*[xх]\s*)?(?<![\d][-/])(\d+(?:[.,]\d+)?)\s*(' + _UNIT_ALT + r')\b'
)

# A money figure may carry BOTH a thousands separator and a decimal separator, in either
# convention: German/Bulgarian "1.393,28" or English "1,393.28", with a plain or
# non-breaking space also used for thousands ("1 393,28"). The number body is captured
# whole and disambiguated by _to_amount below.
_NUM = r'\d{1,3}(?:[.,   ]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,3})?'
_EUR_RE = re.compile(
    rf'(?:({_NUM})\s*€|€\s*({_NUM})|({_NUM})\s*eur\b|\beur\s*({_NUM}))',
    re.IGNORECASE,
)


def _to_amount(raw):
    """Turn a captured money body into a float, resolving separator ambiguity.

    This exists because the naive version silently understated prices by up to 1000x,
    which is the worst possible failure in this pipeline: a EUR 2499 television read as
    EUR 2.499 is below every trigger_eur in the catalog, so it becomes an instant false
    Strong Buy. One parser slip is amplified by every gate downstream.

    Rules, in order:
      - both '.' and ',' present  -> the LAST one is the decimal separator
      - one separator, exactly 3 digits after it -> thousands separator. Retail prices do
        not have three decimal places, so "2.499" is 2499 and "1,234" is 1234.
      - one separator, 1-2 digits after it -> decimal separator
      - spaces and non-breaking spaces are always thousands separators

    >>> _to_amount("1.393,28")
    1393.28
    >>> _to_amount("2.499")
    2499.0
    >>> _to_amount("104,42")
    104.42
    """
    s = raw.strip().replace(" ", "").replace(" ", "").replace(" ", "")
    last_dot, last_comma = s.rfind("."), s.rfind(",")
    if last_dot != -1 and last_comma != -1:
        dec = "." if last_dot > last_comma else ","
        thou = "," if dec == "." else "."
        s = s.replace(thou, "").replace(dec, ".")
    elif last_dot != -1 or last_comma != -1:
        sep = "." if last_dot != -1 else ","
        tail = s.split(sep)[-1]
        s = s.replace(sep, "") if len(tail) == 3 else s.replace(sep, ".")
    try:
        return float(s)
    except ValueError:
        return None

_ISO_DATE_RE = re.compile(r'\b(\d{4})-(\d{2})-(\d{2})\b')
_DMY_DATE_RE = re.compile(r'\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b')


def parse_qty(name):
    """-> (value_in_base_unit, base_unit) or (None, None).

    Handles a leading "N x" / "N х" (Latin or Cyrillic) multiplier, comma or dot
    decimals, and a unit glued to the number or spaced — see catalog.UNIT_ALIASES for
    every recognised spelling. Converts to base units via to_base(); never hardcodes a
    divisor here.
    """
    s = _fold_accents(name)
    m = _QTY_RE.search(s)
    if not m:
        return None, None
    mult = float(m.group(1)) if m.group(1) else 1.0
    value = float(m.group(2).replace(",", "."))
    raw_unit = catalog.UNIT_ALIASES.get(m.group(3))
    if raw_unit is None:
        return None, None
    return to_base(value * mult, raw_unit)


def parse_eur(text):
    """First EUR amount as float. RETURNS None FOR BGN / лв. amounts.

    No BGN parsing branch exists anywhere in this function, by design (see CONTRACT
    §9): a лв./BGN amount simply never matches _EUR_RE, so a string carrying both
    ("12,90 € / 25,23 лв.") returns the EUR figure regardless of order.

    Thousands separators are handled by _to_amount. They must be: an unhandled
    "1.393,28" parses as 393.28 and an unhandled "2.499" as 2.499, understating a price
    by up to 1000x and turning any expensive durable into an instant false Strong Buy.

    >>> parse_eur("1.393,28€")
    1393.28
    >>> parse_eur("2.499€")
    2499.0
    >>> parse_eur("104,42€")
    104.42
    >>> parse_eur("6,84 лв.") is None
    True
    """
    m = _EUR_RE.search(text or "")
    if not m:
        return None
    return _to_amount(next(g for g in m.groups() if g is not None))


def parse_valid_until(text):
    """'Важи до 27.08.2026' -> '2026-08-27'. None when absent.

    Accepts an ISO date already present, or DD.MM.YYYY / DD/MM/YYYY. Two-digit years
    are deliberately unsupported — guessing a century is worse than returning None.
    """
    if not text:
        return None
    m = _ISO_DATE_RE.search(text)
    if m:
        return m.group(0)
    m = _DMY_DATE_RE.search(text)
    if not m:
        return None
    d, mo, y = m.groups()
    return f"{y}-{int(mo):02d}-{int(d):02d}"


def match_sku(offer):
    """-> (sku, sku_class, match_conf) or (None, None, None).

    Iterates catalog.CATALOG in insertion order (consumables first); first match wins.
    An offer matches item X iff some group in X["match"]["any_of"] is a whole-token
    SUBSET of tokens(offer["name"]) AND no X["match"]["none"] word is present.

    THE VETO IS A PREFIX TEST, `any_of` IS EXACT. That asymmetry is deliberate and was
    measured, not guessed — all three candidate semantics were run over the 52 real feed
    titles in fixtures/:

      exact token equality  — LEAKS the smoked-vs-fresh trap. catalog.py writes the veto
        as the bare stem "пушен" but tokens() does no stemming, so "Пушена сьомга" only
        ever yields "пушена"/"pushena" and the cat-food-grade trap gets through.
      substring anywhere    — fixes that trap but SILENTLY VETOES 9 real deals: "spare"
        inside "transparent", "cat" inside "speedcat", "liner" inside "berliner",
        "paper" inside "epaper", "bar" inside "verstellbare". Each is a false negative
        that drops a genuine find with no trace.
      prefix                — fixes the trap with zero spurious hits.

    Prefix is also the linguistically correct tool: Bulgarian inflection is SUFFIXAL, so
    "пушен" prefixes "пушена"/"пушено"/"пушени" and "сьомг" prefixes "сьомгата". Write
    veto entries as stems.

    `any_of` stays strict whole-token per the frozen contract — that is what makes the
    travel repo's substring footgun impossible. The asymmetry is justified because the
    two errors are not symmetric: a missed veto is a FALSE POSITIVE, which costs LLM
    budget and user trust, while a missed match is merely a miss that catalog_health
    surfaces after CATALOG_STALE_RUNS runs.
    """
    toks = tokens(offer.get("name") or "")
    for sku, item in catalog.CATALOG.items():
        rules = item.get("match", {})
        none_words = rules.get("none", [])
        if any(tok.startswith(nw) for tok in toks for nw in none_words):
            continue
        for group in rules.get("any_of", []):
            if set(group).issubset(toks):
                high = len(group) >= 2 or sku in catalog.WISHLIST
                return sku, item.get("class"), ("high" if high else "medium")
    return None, None, None


def _qty_from_net(net_qty, expected_unit):
    """-> (qty, unit) from a source-declared net quantity, or (None, None).

    A source-declared net quantity beats a name parse: it is the manufacturer's
    statutory declaration rather than a guess at what a product title means. It is
    already in base units, so it is used AS IS — no divisor, no conversion. Everything
    that needs one still goes through catalog.UNIT_TO_BASE.

    IT IS TRUSTED FOR kg AND L ONLY. Lidl's `Нетно количество` is a net MASS/VOLUME,
    so on a per-piece sku it reads the weight of the pack, not its count — measured on
    the committed fixture, "Тоалетна хартия 8бр" declares 0.766 and "Colgate Четка за
    зъби 3бр" declares 0.042. Using those as piece counts turns €4.00 for eight rolls
    into "€5.22 per roll", the same 10x class of error the calibre guard exists to
    remove. Per-piece skus keep the name-parsing path, which reads "8бр" correctly.

    Known, accepted imprecision on L: edible oils declare MASS even when sold by
    volume, so a 1 L bottle of sunflower oil reads 0.917 and its €/L comes out ~9%
    HIGH. That is the conservative direction — it understates a discount rather than
    manufacturing one — and it is the source's own labelling, not our arithmetic.
    Water, milk, shampoo and toothpaste all declare volume and are exact.
    """
    if not isinstance(net_qty, (int, float)) or isinstance(net_qty, bool):
        return None, None
    if net_qty <= 0 or expected_unit not in ("kg", "L"):
        return None, None
    return float(net_qty), expected_unit


def annotate(offer):
    """Mutate offer IN PLACE adding sku, sku_class, match_conf, qty, unit,
    unit_price_eur, pending_qty. Return the offer.

    Quantity comes from a source-declared `net_qty` where one is available and
    trustworthy for the sku's unit (see _qty_from_net), and from parsing the product
    name otherwise. `pending_qty` stays False for a net_qty row, so the audit is never
    invited to invent a divisor for a quantity the source already declared."""
    sku, sku_class, match_conf = match_sku(offer)
    offer["sku"] = sku
    offer["sku_class"] = sku_class
    offer["match_conf"] = match_conf

    if sku is None or sku_class == "durable":
        offer["qty"] = None
        offer["unit"] = None
        offer["unit_price_eur"] = None
        offer["pending_qty"] = False
        return offer

    expected_unit = unit_of(sku)
    qty, unit = _qty_from_net(offer.get("net_qty"), expected_unit)

    if qty is None:
        qty, unit = parse_qty(offer.get("name") or "")
        if unit is not None and expected_unit is not None and unit != expected_unit:
            # A litre reading on a per-kilo sku is worse than no reading — treat as a
            # failed parse rather than silently mis-priced.
            qty, unit = None, None

    offer["qty"] = qty
    offer["unit"] = unit
    offer["pending_qty"] = qty is None

    price_eur = offer.get("price_eur")
    if price_eur is not None and qty is not None and qty > 0:
        offer["unit_price_eur"] = round(price_eur / qty, 4)
    else:
        offer["unit_price_eur"] = None

    return offer
