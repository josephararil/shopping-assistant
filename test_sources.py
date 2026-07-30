"""test_sources.py — hand-rolled test harness for sources.py. No pytest, no network.

Every test reads a committed fixture (fixtures/ccc_top_drops.xml, fixtures/mydealz_hot.xml)
or a small synthetic XML string built in this file. Network calls are exercised only via
monkeypatching sources._fetch_text / sources.parse_mydealz.
"""

import sys
sys.stdout.reconfigure(encoding="utf-8")

import config as C
import catalog
import sources

_fails = []


def chk(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _fails.append(name)


CCC_FIXTURE = open("fixtures/ccc_top_drops.xml", encoding="utf-8").read()
MYDEALZ_FIXTURE = open("fixtures/mydealz_hot.xml", encoding="utf-8").read()


# ── parse_ccc ────────────────────────────────────────────────────────────────

ccc_offers = sources.parse_ccc(CCC_FIXTURE)
chk("parse_ccc returns 20 offers", len(ccc_offers) == 20, f"got {len(ccc_offers)}")

# Spot check 1: first item.
o = ccc_offers[0]
chk("ccc[0] name", o["name"] == "Oral-B iO Kids 6+ Disney Stitc... für Zahnpflege, Blau (Stitch)", o["name"])
chk("ccc[0] price_eur", o["price_eur"] == 42.18, o["price_eur"])
chk("ccc[0] was_price_eur", o["was_price_eur"] == 59.37, o["was_price_eur"])
chk("ccc[0] claimed_discount", round(o["claimed_discount"], 2) == 0.29, o["claimed_discount"])
chk("ccc[0] retailer", o["retailer"] == "Amazon.de", o["retailer"])
chk("ccc[0] category_hint is None", o["category_hint"] is None)
chk("ccc[0] source", o["source"] == "ccc")

# Spot check 2: Carson MiniBrite (index 11) — was_price_eur and claimed_discount to 2dp.
o = ccc_offers[11]
chk("ccc[11] name", "Carson MiniBrite" in o["name"], o["name"])
chk("ccc[11] price_eur", o["price_eur"] == 9.21, o["price_eur"])
chk("ccc[11] was_price_eur", o["was_price_eur"] == 13.95, o["was_price_eur"])
chk("ccc[11] claimed_discount to 2dp", round(o["claimed_discount"], 2) == 0.34, o["claimed_discount"])

# Spot check 3: BOSCH (index 5).
o = ccc_offers[5]
chk("ccc[5] price_eur", o["price_eur"] == 193.99, o["price_eur"])
chk("ccc[5] was_price_eur", o["was_price_eur"] == 229.00, o["was_price_eur"])
chk("ccc[5] url", o["url"] == "https://de.camelcamelcamel.com/product/B0946R8WTL", o["url"])

# Spot check 4: COBI MIG-28 (index 19, last item, no "..." truncation in name).
o = ccc_offers[19]
chk("ccc[19] name exact", o["name"] == "COBI MIG-28", o["name"])
chk("ccc[19] price_eur", o["price_eur"] == 32.13, o["price_eur"])
chk("ccc[19] was_price_eur", o["was_price_eur"] == 35.28, o["was_price_eur"])

# The exact shape quoted in the spec: "down 63.33% (104,42€) to 60,46€ from 164,88€".
SYNTHETIC_CCC_GOOD = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item><title>Some Product - down 63.33% (104,42€) to 60,46€ from 164,88€</title>
<link>https://example.com/a</link></item>
<item><title>Second Product - down 10.00% (1,00€) to 9,00€ from 10,00€</title>
<link>https://example.com/b</link></item>
</channel></rss>"""

synth = sources.parse_ccc(SYNTHETIC_CCC_GOOD)
chk("ccc synthetic shape count", len(synth) == 2, len(synth))
chk("ccc synthetic price_eur == 60.46", synth[0]["price_eur"] == 60.46, synth[0]["price_eur"])
chk("ccc synthetic was_price_eur == 164.88", synth[0]["was_price_eur"] == 164.88, synth[0]["was_price_eur"])

# A deliberately malformed title (no " - down " shape) is skipped, not raised, and the
# good items around it still parse.
SYNTHETIC_CCC_MALFORMED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item><title>Good Product One - down 20.00% (2,00€) to 8,00€ from 10,00€</title>
<link>https://example.com/1</link></item>
<item><title>This title has no down-shape at all</title>
<link>https://example.com/2</link></item>
<item><title>Good Product Two - down 50.00% (5,00€) to 5,00€ from 10,00€</title>
<link>https://example.com/3</link></item>
</channel></rss>"""

try:
    malformed_result = sources.parse_ccc(SYNTHETIC_CCC_MALFORMED)
    malformed_raised = False
except Exception:
    malformed_result = []
    malformed_raised = True

chk("ccc malformed title does not raise", not malformed_raised)
chk("ccc malformed title is skipped, good ones survive", len(malformed_result) == 2, len(malformed_result))


# ── parse_mydealz ────────────────────────────────────────────────────────────

myd_offers = sources.parse_mydealz(MYDEALZ_FIXTURE)
chk("parse_mydealz returns 30 offers", len(myd_offers) == 30, f"got {len(myd_offers)}")

o = myd_offers[0]
chk("mydealz[0] heat is int 108", o["heat"] == 108 and isinstance(o["heat"], int), o["heat"])
chk("mydealz[0] name has heat prefix stripped", o["name"] == "Besiege (Steam); PS5 für 5,99 €", o["name"])
chk("mydealz[0] price_eur", o["price_eur"] == 2.95, o["price_eur"])

chk("mydealz[0] category_hint is a CATEGORY_HINTS key",
    o["category_hint"] in catalog.CATEGORY_HINTS, o["category_hint"])

# pepper:merchant with no `name` attribute -> retailer falls back to "mydealz".
o = myd_offers[1]
chk("mydealz[1] merchant has no name -> retailer mydealz", o["retailer"] == "mydealz", o["retailer"])
chk("mydealz[1] price_eur still parsed", o["price_eur"] == 71.99, o["price_eur"])

# Item with no pepper:merchant element at all.
o = myd_offers[3]
chk("mydealz[3] no merchant element -> retailer mydealz", o["retailer"] == "mydealz", o["retailer"])
chk("mydealz[3] no merchant element -> price None", o["price_eur"] is None)

# Item with a named merchant and thousands-separated price (1.393,28€).
o = myd_offers[23]
chk("mydealz[23] thousands-separated price parses correctly", o["price_eur"] == 1393.28, o["price_eur"])
chk("mydealz[23] retailer normalised via alias", o["retailer"] == "Amazon.de", o["retailer"])

# category_hint sanity across the whole parsed set: every non-None value must be a
# real CATEGORY_HINTS key.
bad_hints = [x["category_hint"] for x in myd_offers if x["category_hint"] is not None and x["category_hint"] not in catalog.CATEGORY_HINTS]
chk("mydealz all category_hints are valid CATEGORY_HINTS keys or None", bad_hints == [], bad_hints)


# ── fetch_ccc / fetch_mydealz never raise, and report failures ─────────────

_orig_fetch_text = sources._fetch_text


def _raiser(url, timeout=25):
    raise ConnectionError("boom")


sources._fetch_text = _raiser
offers, report = sources.fetch_ccc()
chk("fetch_ccc with raising _fetch_text returns []", offers == [], offers)
chk("fetch_ccc with raising _fetch_text ok is False", report["ok"] is False, report)
chk("fetch_ccc note carries exception type", "ConnectionError" in report["note"], report["note"])
sources._fetch_text = _orig_fetch_text


# ── harvest() survives a parser raising on the other source ────────────────

_orig_parse_mydealz = sources.parse_mydealz
_orig_fetch_text2 = sources._fetch_text


def _fake_fetch_text(url, timeout=25):
    if "camelcamelcamel" in url:
        return CCC_FIXTURE
    return MYDEALZ_FIXTURE


def _raising_parse_mydealz(xml_text):
    raise ValueError("layout changed")


sources._fetch_text = _fake_fetch_text
sources.parse_mydealz = _raising_parse_mydealz

all_offers, reports = sources.harvest()
by_source = {r["source"]: r for r in reports}
chk("harvest survives mydealz parse raising — ccc offers present", len(all_offers) == 20, len(all_offers))
chk("harvest reports mydealz ok=False", by_source["mydealz"]["ok"] is False, by_source["mydealz"])
chk("harvest reports ccc ok=True", by_source["ccc"]["ok"] is True, by_source["ccc"])

sources.parse_mydealz = _orig_parse_mydealz
sources._fetch_text = _orig_fetch_text2


# ── fewer-than-MIN_EXPECTED_OFFERS still ok=True but with a loud warning ───

SYNTHETIC_CCC_FEW = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item><title>Only Product - down 10.00% (1,00€) to 9,00€ from 10,00€</title>
<link>https://example.com/x</link></item>
</channel></rss>"""


def _fake_fetch_text_few(url, timeout=25):
    return SYNTHETIC_CCC_FEW


sources._fetch_text = _fake_fetch_text_few
offers, report = sources.fetch_ccc()
chk("fetch_ccc below MIN_EXPECTED_OFFERS still ok=True", report["ok"] is True, report)
chk("fetch_ccc below MIN_EXPECTED_OFFERS warns in note",
    "WARNING" in report["note"] and str(C.MIN_EXPECTED_OFFERS["ccc"]) in report["note"], report["note"])
sources._fetch_text = _orig_fetch_text


# ── harvest() offers carry all 11 contract keys + 7 match.annotate keys ─────

CONTRACT_KEYS = {
    "source", "retailer", "name", "price_eur", "was_price_eur", "claimed_discount",
    "valid_until", "url", "heat", "category_hint", "raw",
}
ANNOTATE_KEYS = {"sku", "sku_class", "match_conf", "qty", "unit", "unit_price_eur", "pending_qty"}
ALL_KEYS = CONTRACT_KEYS | ANNOTATE_KEYS


def _fake_fetch_text_both(url, timeout=25):
    if "camelcamelcamel" in url:
        return CCC_FIXTURE
    return MYDEALZ_FIXTURE


sources._fetch_text = _fake_fetch_text_both
all_offers, reports = sources.harvest()
sources._fetch_text = _orig_fetch_text2

missing_key_offers = [o for o in all_offers if not ALL_KEYS.issubset(o.keys())]
chk("harvest offers all carry 11 contract keys + 7 annotate keys",
    missing_key_offers == [], f"{len(missing_key_offers)} offers missing keys")
chk("harvest returns offers for both sources", len(all_offers) == 20 + 30, len(all_offers))


# ── no broshura anywhere in sources.py ──────────────────────────────────────

sources_src = open("sources.py", encoding="utf-8").read()
chk('"broshura" does not appear in sources.py', "broshura" not in sources_src)


if _fails:
    print(f"\n{len(_fails)} failure(s): {_fails}")
else:
    print("\nAll source tests passed.")
sys.exit(1 if _fails else 0)
