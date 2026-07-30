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

bravia = {"name": "Sony Bravia TV 55", "price_eur": 500.0}
chk("Sony Bravia trap: no xm5 match",
    match.match_sku(bravia)[0] != "av.sony_xm5", match.match_sku(bravia))

case = {"name": "Калъф за Sony WH-1000XM5", "price_eur": 15.0}
chk("Sony case trap: no xm5 match",
    match.match_sku(case)[0] != "av.sony_xm5", match.match_sku(case))

nespresso = {"name": "Nespresso капсули кафе 10 бр", "price_eur": 4.0}
chk("Nespresso capsule trap: no coffee-beans match",
    match.match_sku(nespresso)[0] != "food.coffee_beans", match.match_sku(nespresso))

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

sony = {"name": "Sony WH-1000XM5 слушалки", "price_eur": 249.0}
sku, sku_class, conf = match.match_sku(sony)
chk("sony positive match", sku == "av.sony_xm5", (sku, sku_class, conf))
chk("sony match_conf is high (two-token group)", conf == "high", conf)
match.annotate(sony)
chk("sony durable fields all None/False",
    (sony["qty"], sony["unit"], sony["unit_price_eur"], sony["pending_qty"])
    == (None, None, None, False), sony)

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

print(f"\n{len(_fails)} failure(s)" if _fails else "\nAll match tests passed.")
sys.exit(1 if _fails else 0)
