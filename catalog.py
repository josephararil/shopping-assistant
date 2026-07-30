"""catalog.py — DATA ONLY, no logic. The user's watchlist, wishlist and vocabulary.

Separated from config.py deliberately: this is hundreds of lines of data that the user
owns and edits, while config.py holds knobs the pipeline owns. The travel repo's
config.py hit 727 lines by mixing knobs, data and prompts, and became unreviewable.

── WHAT THE USER OWNS ───────────────────────────────────────────────────────────
`par_eur` and `trigger_eur` are USER-OWNED numbers. `par_eur` is the household's
target unit price — the reference every consumable verdict is measured against.
`trigger_eur` is "yes at this price", an absolute pre-commitment that outranks all
discount maths. The pipeline PROPOSES adjustments to par (config.effective_par, clamped
to +/-PAR_DRIFT_MAX) and surfaces a par-review line in the email; it never overwrites
a number here. Weeks 1-4 are calibration: expect to prune this list after run 1.

── HOW MATCHING WORKS (and why it cannot fire on prose) ─────────────────────────
An offer matches item X IFF some group in X["match"]["any_of"] is a SUBSET of
match.tokens(offer["name"]), AND no token in X["match"]["none"] appears in that set.
First match in CATALOG ORDER wins, so order is meaningful: put specific skus above
general ones.

`any_of` groups are AND-SETS OF WHOLE TOKENS, never substrings. ["sony","xm5"] cannot
fire on "Sony TV" or on a stray "XM5" alone. The travel repo's `alias.lower() in
lookup` would match "Sony" inside "Sony Center Berlin"; this cannot.

`none` is a HARD VETO and is where accumulated real-world knowledge lives — the
cat-food trap, the phone-case trap, the smoked-vs-fresh trap. EVERY FALSE POSITIVE
EVER SEEN BECOMES ONE TOKEN HERE. That is the maintenance loop.

match.tokens() unions three views of a name, so one rule set serves Bulgarian, German
and English sources:
  - folded:  accents stripped, Cyrillic transliterated, punctuation -> SPACE
             "Пушена сьомга" -> {pushena, syomga};  "WH-1000XM5" -> {wh, 1000xm5}
  - raw:     lowercased, split on whitespace       -> {wh-1000xm5}
  - squashed: punctuation REMOVED, not spaced      -> {wh1000xm5}
Write rules in whichever script is natural; all three hit.

KNOWN LIMITATION, ACCEPTED: Bulgarian inflection (сьомга / сьомгата / сьомгови) are
different tokens and v1 does NOT stem. False positives are far worse than false
negatives here, because a false positive costs budget AND trust. A sku that has never
matched in CATALOG_STALE_RUNS runs is surfaced in the email as a rule to fix.
"""

# ── Consumables: recurring purchases with a target UNIT price ────────────────
# unit is exactly one of "kg" | "L" | "pc". par_eur is EUR per that unit.
# bulk_qty is what "buy and stock up" means for this item — it drives the saving
# figure the user actually reasons with ("buy 5 kg, saves EUR 11").
# restock_days is how long a stock-up lasts, and IS the anti-spam TTL for this item.
WATCHLIST = {
    "food.salmon_fillet": {
        "class": "consumable", "label": "Salmon fillet / steak", "unit": "kg",
        "par_eur": 12.00, "bulk_qty": 5.0, "restock_days": 90, "freezable": True,
        "bulk_note": "Portion into 400 g bags, freeze; keeps 3 months.",
        "match": {
            "any_of": [["сьомга"], ["salmon"], ["losos"], ["lachs"]],
            "none": ["пастет", "pate", "котк", "cat", "куче", "dog",
                     "пушен", "smoked", "gerauchert", "консерв", "spread", "храна"],
        },
    },
    "food.chicken_breast": {
        "class": "consumable", "label": "Chicken breast fillet", "unit": "kg",
        "par_eur": 6.00, "bulk_qty": 6.0, "restock_days": 60, "freezable": True,
        "bulk_note": "Freeze in 500 g portions; keeps 4 months.",
        "match": {
            "any_of": [["пилешко", "филе"], ["chicken", "breast"], ["huhnerbrust"],
                       ["пиле", "филе"]],
            "none": ["котк", "cat", "куче", "dog", "храна", "пастет", "pate",
                     "нагетс", "nuggets", "паниран", "breaded", "консерв"],
        },
    },
    "food.olive_oil": {
        "class": "consumable", "label": "Extra virgin olive oil", "unit": "L",
        "par_eur": 9.00, "bulk_qty": 5.0, "restock_days": 180, "freezable": False,
        "bulk_note": "Keeps 2 years unopened; store dark and cool.",
        "match": {
            "any_of": [["маслиново", "масло"], ["olive", "oil"], ["olivenol"],
                       ["зехтин"]],
            "none": ["сапун", "soap", "козметик", "cosmetic", "маслини", "olives",
                     "спрей", "spray", "шампоан", "shampoo"],
        },
    },
    "food.coffee_beans": {
        "class": "consumable", "label": "Coffee beans / ground coffee", "unit": "kg",
        "par_eur": 14.00, "bulk_qty": 3.0, "restock_days": 120, "freezable": False,
        "bulk_note": "Whole beans keep 6 months sealed; ground goes flat in weeks.",
        "match": {
            "any_of": [["кафе", "зърна"], ["coffee", "beans"], ["kaffeebohnen"],
                       ["кафе", "мляно"], ["coffee", "ground"]],
            "none": ["капсул", "capsule", "dolce", "nespresso", "разтворим",
                     "instant", "3in1", "машина", "machine", "мляко", "milk"],
        },
    },
    "supp.whey_protein": {
        # The named annual-promo case: restock_days ~300 so the yearly silabg promo
        # re-alerts on its own cycle instead of being suppressed by a global TTL.
        "class": "consumable", "label": "Whey protein powder", "unit": "kg",
        "par_eur": 19.00, "bulk_qty": 50.0, "restock_days": 300, "freezable": False,
        "bulk_note": "Sealed tubs keep 18-24 months. The yearly promo is the buy window.",
        "match": {
            "any_of": [["whey"], ["протеин", "whey"], ["суроватъчен"], ["wheyprotein"]],
            "none": ["bar", "барче", "шейк", "готов", "ready", "drink", "напитка",
                     "vegan", "веган", "casein", "казеин", "gainer", "гейнър"],
        },
    },
    "house.laundry_gel": {
        "class": "consumable", "label": "Laundry detergent gel", "unit": "L",
        "par_eur": 4.50, "bulk_qty": 10.0, "restock_days": 120, "freezable": False,
        "bulk_note": "Indefinite shelf life; only storage space limits the buy.",
        "match": {
            "any_of": [["течен", "перилен"], ["liquid", "detergent"], ["waschgel"],
                       ["гел", "пране"], ["laundry", "gel"]],
            "none": ["омекотител", "softener", "капсул", "capsule", "podove",
                     "прах", "powder", "съдомиял", "dishwasher", "препарат", "съдове"],
        },
    },
}

# ── Durables: one-off purchases with an absolute "yes at this price" trigger ──
# No par_eur and no unit: a durable is judged on discount against a CREDIBLE reference
# plus absolute saving, or bought outright at trigger_eur. Because there is no
# user-owned par, durables get no `user_par` evidence leg — a lone retailer claim
# leaves them far below MIN_EVIDENCE_STRONG, which is the point.
WISHLIST = {
    "av.sony_xm5": {
        "class": "durable", "label": "Sony WH-1000XM5 headphones",
        "trigger_eur": 200.00, "restock_days": 730,
        "match": {
            "any_of": [["sony", "xm5"], ["sony", "wh1000xm5"], ["wh1000xm5"],
                       ["sony", "wh", "1000xm5"]],
            "none": ["case", "hulle", "калъф", "pads", "earpads", "cable", "кабел",
                     "stand", "стойка", "xm4", "xm3"],
        },
    },
    "tech.robot_vacuum": {
        "class": "durable", "label": "Robot vacuum with mop + self-empty base",
        "trigger_eur": 220.00, "restock_days": 1825,
        "match": {
            "any_of": [["робот", "прахосмукачка"], ["robot", "vacuum"],
                       ["saugroboter"], ["roborock"], ["dreame"], ["robotska"]],
            "none": ["чанта", "bag", "торбичк", "филтър", "filter", "четка",
                     "brush", "мопа", "части", "spare", "accessor"],
        },
    },
    "tech.nas_hdd_4tb": {
        "class": "durable", "label": "NAS-grade 4 TB hard drive",
        "trigger_eur": 90.00, "restock_days": 365,
        "match": {
            "any_of": [["ironwolf", "4tb"], ["wd", "red", "4tb"], ["nas", "4tb"],
                       ["hdd", "4tb"], ["4tb", "nas"]],
            "none": ["ssd", "външен", "external", "usb", "кутия", "enclosure",
                     "2tb", "8tb", "docking"],
        },
    },
    "kitchen.airfryer": {
        "class": "durable", "label": "Air fryer, 5 L+ dual or single basket",
        "trigger_eur": 90.00, "restock_days": 1825,
        "match": {
            "any_of": [["airfryer"], ["air", "fryer"], ["heisluftfritteuse"],
                       ["горещ", "въздух", "фритюрник"], ["ninja", "airfryer"]],
            "none": ["кошница", "basket", "части", "spare", "accessor", "хартия",
                     "paper", "liner", "мазнина", "oil"],
        },
    },
}

# Every sku the pipeline knows, consumables first so a consumable rule wins a tie.
CATALOG = {**WATCHLIST, **WISHLIST}

# ── Retailer normalisation ──────────────────────────────────────────────────
# Feeds spell the same chain many ways. Everything keys on the normalised display
# name; the raw string never reaches a key. Lookup is on the LOWERCASED raw value.
RETAILER_ALIASES = {
    "lidl": "Lidl", "lidl bg": "Lidl", "lidl.bg": "Lidl", "lidl bulgaria": "Lidl",
    "kaufland": "Kaufland", "kaufland bg": "Kaufland", "kaufland.bg": "Kaufland",
    "billa": "Billa", "billa bg": "Billa", "billa.bg": "Billa",
    "metro": "Metro", "metro bg": "Metro", "metro bulgaria": "Metro",
    "metro cash & carry": "Metro", "метро": "Metro",
    "technopolis": "Technopolis", "технополис": "Technopolis",
    "zora": "Zora", "зора": "Zora", "technomarket": "Technomarket",
    "jysk": "JYSK", "ikea": "IKEA",
    "amazon": "Amazon.de", "amazon.de": "Amazon.de", "amazon de": "Amazon.de",
    "silabg": "silabg", "silabg.com": "silabg", "sila bg": "silabg",
    "mydealz": "mydealz",
    "fantastico": "Fantastico", "фантастико": "Fantastico",
    "t market": "T MARKET", "tmarket": "T MARKET",
}

# Email section order. Retailers not listed here sort after these, alphabetically.
# Within a retailer, items sort Strong Buy first then Fair, by rank_score.
RETAILER_ORDER = [
    "Metro", "Kaufland", "Lidl", "Billa", "Fantastico", "T MARKET",
    "silabg", "Amazon.de", "Technopolis", "Technomarket", "Zora",
    "JYSK", "IKEA", "mydealz",
]

# ── Category hints -> class ─────────────────────────────────────────────────
# Used ONLY by the off-list discovery path, to decide whether an unmatched offer is a
# plausible durable. Off-list CONSUMABLE discovery is cut entirely: with no par there
# is nothing to compute a unit price against. A hint that maps to "consumable" or is
# absent therefore kills the lead at `no_sku_match`.
CATEGORY_HINTS = {
    # mydealz <category> values
    "elektronik": "durable",
    "haushalt & wohnen": "durable",
    "computer & zubehor": "durable",
    "gaming": "durable",
    "heimwerken & garten": "durable",
    "lebensmittel & haushalt": "consumable",
    "drogerie & gesundheit": "consumable",
    "beauty & gesundheit": "consumable",
    # broshura.bg / camelcamelcamel-ish groupings
    "electronics": "durable",
    "техника": "durable",
    "електроника": "durable",
    "бяла техника": "durable",
    "мебели": "durable",
    "инструменти": "durable",
    "хранителни стоки": "consumable",
    "хранителни": "consumable",
    "напитки": "consumable",
    "битова химия": "consumable",
    "козметика": "consumable",
}

# ── Unit vocabulary ─────────────────────────────────────────────────────────
# Maps every spelling a source may use to a canonical raw unit. match.parse_qty then
# converts to the sku's unit via UNIT_TO_BASE: g -> kg, ml -> L, pcs -> pc.
# Keys are matched against the LOWERCASED, accent-folded token.
UNIT_ALIASES = {
    # mass
    "кг": "kg", "kg": "kg", "kilo": "kg", "kilogram": "kg", "килограм": "kg",
    "г": "g", "гр": "g", "g": "g", "gr": "g", "gram": "g", "грам": "g",
    # volume
    "л": "L", "l": "L", "lt": "L", "liter": "L", "litre": "L", "литър": "L", "литра": "L",
    "мл": "ml", "ml": "ml", "cl": "cl",
    # count
    "бр": "pcs", "бр.": "pcs", "броя": "pcs", "брой": "pcs",
    "pcs": "pcs", "pc": "pcs", "pieces": "pcs", "stuck": "pcs", "stk": "pcs",
    "x": "pcs",
}

# Canonical raw unit -> (base unit, multiplier to reach it). The ONLY place these
# divisors exist. A second copy is how "100 g" becomes "1.09 EUR/kg".
UNIT_TO_BASE = {
    "kg": ("kg", 1.0),
    "g":  ("kg", 0.001),
    "L":  ("L", 1.0),
    "ml": ("L", 0.001),
    "cl": ("L", 0.01),
    "pcs": ("pc", 1.0),
}
