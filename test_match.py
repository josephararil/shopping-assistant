"""test_match.py — hand-rolled harness for match.py's new functions. No pytest, no I/O."""

import sys

import match

# Test names/details contain Cyrillic. A Windows console defaulting to cp1252 can't
# encode it and would crash the print below with UnicodeEncodeError — reconfigure to
# utf-8 so `python test_match.py` behaves the same on every platform.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_fails = []


def chk(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _fails.append(name)


# ── parse_qty ────────────────────────────────────────────────────────────────
chk("parse_qty '1 кг'", match.parse_qty("1 кг") == (1.0, "kg"), match.parse_qty("1 кг"))
chk("parse_qty '500 г'", match.parse_qty("500 г") == (0.5, "kg"), match.parse_qty("500 г"))
chk("parse_qty '0,5 л'", match.parse_qty("0,5 л") == (0.5, "L"), match.parse_qty("0,5 л"))
chk("parse_qty '2 x 0.5 L'", match.parse_qty("2 x 0.5 L") == (1.0, "L"), match.parse_qty("2 x 0.5 L"))
chk("parse_qty '6х1.5л'", match.parse_qty("6х1.5л") == (9.0, "L"), match.parse_qty("6х1.5л"))
chk("parse_qty '250ml'", match.parse_qty("250ml") == (0.25, "L"), match.parse_qty("250ml"))
chk("parse_qty '4 бр'", match.parse_qty("4 бр") == (4.0, "pc"), match.parse_qty("4 бр"))
chk("parse_qty '6 Stück'", match.parse_qty("6 Stück") == (6.0, "pc"), match.parse_qty("6 Stück"))
chk("parse_qty nothing parses", match.parse_qty("Sony WH-1000XM5 слушалки") == (None, None))

# ── parse_qty: the CALIBRE GUARD ─────────────────────────────────────────────
# "Боб насипен 200-220/100 г" is a Bulgarian grading notation — 200-220 beans per
# 100 g — on a product sold loose by the kilo. Read as a 100 g pack it turned a
# €1.69/kg bag of beans into €16.90/kg, which the prefilter then rejected as over_par:
# a 10x parse error discarding the best find of the week before any LLM saw it. The
# guard rejects a number preceded by digit-then-hyphen-or-slash and nothing else.
chk("calibre guard: '200-220/100 г' is not a 100 g pack",
    match.parse_qty("Боб насипен 200-220/100 г") == (None, None),
    match.parse_qty("Боб насипен 200-220/100 г"))
chk("calibre guard: '30/40г' grading is rejected too",
    match.parse_qty("Билков чай SK1, 30/40г") == (None, None),
    match.parse_qty("Билков чай SK1, 30/40г"))
# ...and it must not cost any legitimate parse. Each of these has a digit somewhere
# before the quantity; none is preceded by digit-hyphen or digit-slash.
chk("calibre guard does not break a multiplier", match.parse_qty("2x500 g") == (1.0, "kg"),
    match.parse_qty("2x500 g"))
chk("calibre guard does not break a percentage prefix",
    match.parse_qty("Прясно мляко 3,5% 1 л") == (1.0, "L"),
    match.parse_qty("Прясно мляко 3,5% 1 л"))
chk("calibre guard does not break a model number",
    match.parse_qty("Шампоан ES 400 ml SK 2") == (0.4, "L"),
    match.parse_qty("Шампоан ES 400 ml SK 2"))

# ── annotate — a source-declared net_qty overrides the name parse ────────────
# Lidl's statutory export declares `Нетно количество` per row. It is the manufacturer's
# own figure, so it beats any guess at what a product title means — and it settles both
# of the parse failures above with authority rather than with a regex.
beans = {"name": "Боб насипен 200-220/100 г", "price_eur": 2.04, "net_qty": 1.0}
match.annotate(beans)
chk("net_qty overrides a name parse: beans are €2.04/kg, not €20.40/kg",
    (beans["qty"], beans["unit"], beans["unit_price_eur"]) == (1.0, "kg", 2.04), beans)
chk("a net_qty row is never pending_qty — the audit must not invent a divisor",
    beans["pending_qty"] is False)

drained = {"name": "Боб кидни (ОНТ 290g)", "price_eur": 1.26, "net_qty": 0.42}
match.annotate(drained)
chk("net_qty wins over a drained-weight figure in the name: 0.42, not 0.29",
    drained["qty"] == 0.42, drained)

kashkaval = {"name": "Milki Dream Кашкавал", "price_eur": 7.75, "net_qty": 0.40}
match.annotate(kashkaval)
chk("net_qty supplies a quantity no name parse could find",
    (kashkaval["qty"], kashkaval["unit_price_eur"]) == (0.4, 19.375), kashkaval)

# ── annotate — net_qty is NEVER used for a per-piece sku ─────────────────────
# `Нетно количество` is a net MASS/VOLUME, never a count. Measured on the committed
# fixture: "Тоалетна хартия 8бр" declares 0.766 (kilograms of paper) and "Colgate Четка
# за зъби 3бр" declares 0.042. Using those as piece counts turns €3.06 for eight rolls
# into "€4.00 per roll" — the same 10x class of error the calibre guard removes, in the
# opposite direction. Per-piece skus keep the name-parsing path, which reads "8бр".
paper = {"name": "Тоалетна хартия 8бр", "price_eur": 3.06, "net_qty": 0.766}
match.annotate(paper)
chk("net_qty ignored on a pc sku: 8 rolls at €0.3825, not 0.766 'pieces'",
    (paper["qty"], paper["unit"], paper["unit_price_eur"]) == (8.0, "pc", 0.3825), paper)

eggs = {"name": "Яйца размер M 10 бр", "price_eur": 3.01, "net_qty": 0.63}
match.annotate(eggs)
chk("net_qty ignored on eggs: 10 eggs at €0.301, not 0.63 'pieces' of egg",
    (eggs["qty"], eggs["unit"], eggs["unit_price_eur"]) == (10.0, "pc", 0.301), eggs)

# Repointed from the deleted baby.wipes sku onto house.toilet_paper — the assertion is
# about the `pc` PATH, so it needs a name that still resolves to a surviving pc sku.
# An unmatched name would pass this check for the wrong reason: annotate() early-returns
# on sku is None, so qty would be None and pending_qty False, and the net-mass rule would
# never be reached at all.
no_count = {"name": "Тоалетна хартия трипластова XXL", "price_eur": 2.55, "net_qty": 1.64}
match.annotate(no_count)
chk("a pc sku with no count in its name stays pending, rather than using net mass",
    no_count["sku"] == "house.toilet_paper" and no_count["qty"] is None
    and no_count["pending_qty"] is True, no_count)

# An unmatched offer (no sku) never gets a quantity from net_qty, only from a name parse.
unmatched_net = {"name": "Random unrelated product", "price_eur": 249.0, "net_qty": 0.25}
match.annotate(unmatched_net)
chk("net_qty is ignored when the sku is None",
    unmatched_net["qty"] is None and unmatched_net["unit_price_eur"] is None, unmatched_net)

# Junk values must fall through to the name parse, not divide by ~zero.
for bad in (0, -1.0, "1.0", None, True):
    junk = {"name": "Сьомга филе, прясна, 500 г", "price_eur": 6.0, "net_qty": bad}
    match.annotate(junk)
    chk(f"net_qty={bad!r} falls back to the name parse", junk["qty"] == 0.5, junk)

# ── parse_eur ────────────────────────────────────────────────────────────────
chk("parse_eur '104,42€'", match.parse_eur("104,42€") == 104.42)
chk("parse_eur '6,84 лв.' is None (no BGN)", match.parse_eur("6,84 лв.") is None)
chk("parse_eur '€60,46'", match.parse_eur("€60,46") == 60.46)
chk("parse_eur '60.46 EUR'", match.parse_eur("60.46 EUR") == 60.46)
chk("parse_eur 'EUR 60,46'", match.parse_eur("EUR 60,46") == 60.46)
chk("parse_eur picks EUR over лв in same string",
    match.parse_eur("12,90 € / 25,23 лв.") == 12.90)

# ── parse_valid_until ────────────────────────────────────────────────────────
chk("parse_valid_until 'Важи до 27.08.2026'",
    match.parse_valid_until("Важи до 27.08.2026") == "2026-08-27")
chk("parse_valid_until 'валидна до 01/09/2026'",
    match.parse_valid_until("валидна до 01/09/2026") == "2026-09-01")
chk("parse_valid_until 'до 27.08.2026'",
    match.parse_valid_until("до 27.08.2026") == "2026-08-27")
chk("parse_valid_until ISO passthrough",
    match.parse_valid_until("valid 2026-08-27") == "2026-08-27")
chk("parse_valid_until two-digit year is None",
    match.parse_valid_until("Важи до 27.08.26") is None)
chk("parse_valid_until absent is None", match.parse_valid_until("no date here") is None)

# ── match_sku / annotate — negative traps ───────────────────────────────────
cat_food = {"name": "Пастет за котки със сьомга 100 г", "price_eur": 1.50}
chk("cat-food trap: no salmon match",
    match.match_sku(cat_food)[0] != "food.salmon_fillet", match.match_sku(cat_food))

smoked = {"name": "Пушена сьомга 200 г", "price_eur": 3.00}
chk("smoked-vs-fresh trap: no salmon match",
    match.match_sku(smoked)[0] != "food.salmon_fillet", match.match_sku(smoked))

# Repointed from the deleted food.coffee_beans onto its successor food.coffee_ground.
# The name carries BOTH "кафе" and "мляно" deliberately, so it satisfies an any_of group
# and can only be stopped by the veto. The old name ("Nespresso капсули кафе 10 бр") was
# measured to pass for two wrong reasons at once: food.coffee_beans no longer exists, AND
# a bare "кафе" never satisfies food.coffee_ground's two-token groups anyway — so with
# BOTH "капсул" and "nespresso" deleted from the veto list it still returned None. A
# negative test that cannot fail guards nothing.
nespresso = {"name": "Nespresso капсули мляно кафе 10 бр", "price_eur": 4.0}
chk("Nespresso capsule trap: no ground-coffee match",
    match.match_sku(nespresso)[0] != "food.coffee_ground", match.match_sku(nespresso))

# ── match_sku / annotate — positive matches ─────────────────────────────────
salmon = {"name": "Сьомга филе, прясна, 1 кг", "price_eur": 11.40}
sku, sku_class, conf = match.match_sku(salmon)
chk("salmon positive match", sku == "food.salmon_fillet", (sku, sku_class, conf))
chk("salmon match_conf is medium (single-token group)", conf == "medium", conf)
match.annotate(salmon)
chk("salmon annotate qty/unit", (salmon["qty"], salmon["unit"]) == (1.0, "kg"), salmon)
chk("salmon unit_price_eur computed correctly",
    salmon["unit_price_eur"] == round(11.40 / 1.0, 4), salmon["unit_price_eur"])
chk("salmon pending_qty False", salmon["pending_qty"] is False)

olive = {"name": "Olivenöl extra vergine 0,5 L", "price_eur": 4.75}
sku, sku_class, conf = match.match_sku(olive)
chk("olive oil positive match", sku == "food.olive_oil", (sku, sku_class, conf))
match.annotate(olive)
chk("olive oil qty/unit", (olive["qty"], olive["unit"]) == (0.5, "L"), olive)
chk("olive oil unit_price_eur computed correctly",
    olive["unit_price_eur"] == round(4.75 / 0.5, 4), olive["unit_price_eur"])

# ── annotate — unmatched offer: all seven keys present, None/False ─────────
unmatched = {"name": "Random unrelated product 3 бр", "price_eur": 9.99}
match.annotate(unmatched)
chk("unmatched offer: all seven keys None/False",
    (unmatched["sku"], unmatched["sku_class"], unmatched["match_conf"],
     unmatched["qty"], unmatched["unit"], unmatched["unit_price_eur"],
     unmatched["pending_qty"]) == (None, None, None, None, None, None, False),
    unmatched)

# ── annotate — pending_qty when sku matches but qty fails to parse ─────────
pending = {"name": "Сьомга филе прясна", "price_eur": 8.0}
match.annotate(pending)
chk("pending_qty True when sku matched but qty unparseable",
    pending["pending_qty"] is True and pending["qty"] is None, pending)
chk("unit_price_eur None when qty missing", pending["unit_price_eur"] is None)

# ── annotate — consumable unit mismatch is treated as failed parse ─────────
mismatched = {"name": "Сьомга филе, прясна, 0,5 л", "price_eur": 6.0}
match.annotate(mismatched)
chk("consumable unit mismatch (L on a kg sku) discarded as failed parse",
    mismatched["qty"] is None and mismatched["unit"] is None and mismatched["pending_qty"] is True,
    mismatched)


# ── Veto semantics: prefix, not substring-anywhere ───────────────────────────
# Measured over the 52 real titles in fixtures/: substring-anywhere silently vetoed 9
# genuine deals ("spare" inside "transparent", "cat" inside "speedcat", "liner" inside
# "berliner"). Exact-token equality leaked the smoked-salmon trap. These assert the
# prefix behaviour that fixes the trap without the false negatives — if someone widens
# the veto back to substring-anywhere, the three MUST-MATCH rows below go red.
def _m(nm):
    return match.match_sku({"name": nm})[0]

chk("veto is suffix-tolerant: 'пушен' vetoes 'Пушена сьомга'",
    _m("Пушена сьомга 200 г") != "food.salmon_fillet")
chk("veto is NOT substring-anywhere: 'bar' must not veto via 'verstellbare'",
    _m("Whey Protein verstellbare Portionsdose 1kg") == "supp.whey_protein",
    f"got {_m('Whey Protein verstellbare Portionsdose 1kg')}")
chk("veto is NOT substring-anywhere: 'cat' must not veto via 'speedcat'",
    _m("Сьомга филе прясна speedcat edition 1 кг") == "food.salmon_fillet",
    f"got {_m('Сьомга филе прясна speedcat edition 1 кг')}")


# ── parse_eur thousands separators ───────────────────────────────────────────
# A naive parse_eur understated prices by up to 1000x: "1.393,28" came back as 393.28
# and "2.499" as 2.499. That is the worst failure available in this pipeline — a pack
# priced €2499 read as €2.499 divides down to a unit price 1000x under any reference, so
# it becomes an instant false Strong Buy and no downstream gate can stop it. Found in the
# real mydealz feed (LG gram laptop at 1.393,28 €).
for _txt, _want in [
    ("1.393,28€", 1393.28),        # German: dot thousands, comma decimal
    ("2.499€", 2499.0),            # dot + exactly 3 digits = thousands, not 3 decimals
    ("1 393,28 €", 1393.28),       # space thousands
    ("EUR 1.049,99", 1049.99),
    ("€1.234.567,89", 1234567.89),  # several thousands groups
    ("1,393.28€", 1393.28),        # English convention: the LAST separator is the decimal
    ("104,42€", 104.42),           # plain comma decimal still works
    ("60,46€", 60.46),
]:
    chk(f"parse_eur({_txt!r}) == {_want}", match.parse_eur(_txt) == _want,
        f"got {match.parse_eur(_txt)!r}")

chk("parse_eur still returns None for a BGN-only amount",
    match.parse_eur("6,84 лв.") is None, f"got {match.parse_eur('6,84 лв.')!r}")
chk("parse_eur picks EUR when both currencies are present",
    match.parse_eur("12,90 € / 25,23 лв.") == 12.90)

print(f"\n{len(_fails)} failure(s): {_fails}" if _fails else "\nAll match tests passed.")
sys.exit(1 if _fails else 0)
