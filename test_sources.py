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


def _raiser_bytes(url, timeout=25):
    raise ConnectionError("lidl unreachable")


_orig_fetch_bytes = sources._fetch_bytes

sources._fetch_text = _fake_fetch_text
sources.parse_mydealz = _raising_parse_mydealz
sources._fetch_bytes = _raiser_bytes

all_offers, reports, regular_rows = sources.harvest()
by_source = {r["source"]: r for r in reports}
chk("harvest survives mydealz parse raising — ccc offers present", len(all_offers) == 20, len(all_offers))
chk("harvest reports mydealz ok=False", by_source["mydealz"]["ok"] is False, by_source["mydealz"])
chk("harvest reports ccc ok=True", by_source["ccc"]["ok"] is True, by_source["ccc"])
chk("harvest regular_rows == [] when lidl fails entirely", regular_rows == [], regular_rows)
chk("harvest reports lidl ok=False when both URLs fail", by_source["lidl"]["ok"] is False, by_source["lidl"])

sources.parse_mydealz = _orig_parse_mydealz
sources._fetch_text = _orig_fetch_text2
sources._fetch_bytes = _orig_fetch_bytes


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
sources._fetch_bytes = _raiser_bytes  # lidl not under test here — keep it off the network
all_offers, reports, regular_rows = sources.harvest()
sources._fetch_text = _orig_fetch_text2
sources._fetch_bytes = _orig_fetch_bytes

missing_key_offers = [o for o in all_offers if not ALL_KEYS.issubset(o.keys())]
chk("harvest offers all carry 11 contract keys + 7 annotate keys",
    missing_key_offers == [], f"{len(missing_key_offers)} offers missing keys")
chk("harvest returns offers for both sources", len(all_offers) == 20 + 30, len(all_offers))


# ── Lidl BG statutory price export ──────────────────────────────────────────

import zipfile
from io import BytesIO

LIDL_FIXTURE = open("fixtures/lidl_plovdiv.xlsx", "rb").read()

lidl_promo, lidl_regular = sources.parse_lidl(LIDL_FIXTURE)

chk("parse_lidl fixture yields 26 promo offers", len(lidl_promo) == 26, len(lidl_promo))
chk("parse_lidl fixture yields 226 distinct regular products",
    len(lidl_regular) == 226, len(lidl_regular))

# The four verified Plovdiv promo pairs (Цена -> Цена в промоция), claimed_discount to 2dp.
VERIFIED_PAIRS = {
    "Домати на клонка на кг": (1.78, 0.99),
    "Железница Кашкавал от краве мляко": (9.71, 7.75),
    "Немско масло": (2.49, 1.45),
    "Nashe Selo Краве сирене": (7.15, 5.99),
}
lidl_promo_by_name = {o["name"]: o for o in lidl_promo}
for name, (was, now) in VERIFIED_PAIRS.items():
    o = lidl_promo_by_name.get(name)
    chk(f"lidl promo present: {name}", o is not None, name)
    if o is not None:
        chk(f"lidl promo price_eur: {name}", o["price_eur"] == now, o["price_eur"])
        chk(f"lidl promo was_price_eur: {name}", o["was_price_eur"] == was, o["was_price_eur"])
        expected_discount = round(1 - now / was, 2)
        chk(f"lidl claimed_discount to 2dp: {name}",
            round(o["claimed_discount"], 2) == expected_discount, o["claimed_discount"])

# Contract shape: all 11 keys, source == "lidl", retailer == "Lidl", no validity date.
LIDL_CONTRACT_KEYS = {
    "source", "retailer", "name", "price_eur", "was_price_eur", "claimed_discount",
    "valid_until", "url", "heat", "category_hint", "raw",
}
bad_offers = [o for o in lidl_promo if not LIDL_CONTRACT_KEYS.issubset(o.keys())]
chk("lidl promo offers all carry the 11 contract keys", bad_offers == [], len(bad_offers))
chk("lidl promo offers all source=='lidl'", all(o["source"] == "lidl" for o in lidl_promo))
chk("lidl promo offers all retailer=='Lidl'", all(o["retailer"] == "Lidl" for o in lidl_promo))
chk("lidl promo offers all valid_until is None — export carries no validity date",
    all(o["valid_until"] is None for o in lidl_promo))

# No regular row is an offer shape, and no promo price ever appears in regular_rows —
# check on a product code that genuinely appears in BOTH series.
regular_by_code = {r["product_code"]: r for r in lidl_regular}
overlap_codes = [o["product_code"] for o in lidl_promo if o["product_code"] in regular_by_code]
chk("at least one product appears in both promo and regular series (fixture sanity)",
    len(overlap_codes) > 0, len(overlap_codes))
for o in lidl_promo:
    code = o["product_code"]
    reg = regular_by_code.get(code)
    if reg is not None:
        chk(f"lidl regular price for {code} is the shelf price, not the promo price",
            reg["price_eur"] != o["price_eur"], (reg["price_eur"], o["price_eur"]))
        chk(f"lidl regular price for {code} equals was_price_eur",
            reg["price_eur"] == o["was_price_eur"], (reg["price_eur"], o["was_price_eur"]))
chk("no regular row carries the 11-key offer shape",
    all("source" not in r for r in lidl_regular))


def _build_xlsx(row_xmls):
    """Minimal xlsx blob matching the real export's shape: empty sharedStrings,
    inlineStr cells, columns B-H. Used to test edge cases the committed fixture
    doesn't happen to contain."""
    header = (
        '<row r="1">'
        '<c r="B1" t="inlineStr"><is><t>Код</t></is></c>'
        '<c r="C1" t="inlineStr"><is><t>Търговски обект</t></is></c>'
        '<c r="D1" t="inlineStr"><is><t>Наименование на продукта</t></is></c>'
        '<c r="E1" t="inlineStr"><is><t>Код на продукта</t></is></c>'
        '<c r="F1" t="inlineStr"><is><t>Категория</t></is></c>'
        '<c r="G1" t="inlineStr"><is><t>Цена</t></is></c>'
        '<c r="H1" t="inlineStr"><is><t>Цена в промоция</t></is></c>'
        "</row>"
    )
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>" + header + "".join(row_xmls) + "</sheetData></worksheet>"
    )
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "xl/sharedStrings.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<sst count="0" uniqueCount="0" '
            'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>',
        )
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buf.getvalue()


# ── column-letter mapping survives an omitted trailing cell (guard the exact bug) ──
# Row 2: H (Цена в промоция) is OMITTED entirely — not present, not empty — because
# it is the trailing empty cell. Positional indexing would read only 6 cells and
# silently treat "Категория" as if it were "Цена", or similar off-by-one shifts.
# Row 3: both G and H are omitted (two trailing empty cells).
SYNTHETIC_ROW_H_OMITTED = (
    '<row r="2">'
    '<c r="B2" t="inlineStr"><is><t>11111</t></is></c>'
    '<c r="C2" t="inlineStr"><is><t>999 - Пловдив/тест 1</t></is></c>'
    '<c r="D2" t="inlineStr"><is><t>Тестов продукт А</t></is></c>'
    '<c r="E2" t="inlineStr"><is><t>ТЕСТ001</t></is></c>'
    '<c r="F2" t="inlineStr"><is><t>42</t></is></c>'
    '<c r="G2" t="inlineStr"><is><t>5.50</t></is></c>'
    "</row>"
)
SYNTHETIC_ROW_G_H_OMITTED = (
    '<row r="3">'
    '<c r="B3" t="inlineStr"><is><t>22222</t></is></c>'
    '<c r="C3" t="inlineStr"><is><t>999 - Пловдив/тест 1</t></is></c>'
    '<c r="D3" t="inlineStr"><is><t>Тестов продукт Б</t></is></c>'
    '<c r="E3" t="inlineStr"><is><t>ТЕСТ002</t></is></c>'
    '<c r="F3" t="inlineStr"><is><t>7</t></is></c>'
    "</row>"
)
synthetic_blob = _build_xlsx([SYNTHETIC_ROW_H_OMITTED, SYNTHETIC_ROW_G_H_OMITTED])
synthetic_rows = sources._parse_xlsx_rows(synthetic_blob)

chk("_parse_xlsx_rows skips the header and returns 2 data rows",
    len(synthetic_rows) == 2, len(synthetic_rows))

r2 = synthetic_rows[0]
chk("omitted-H row: F (Категория) maps to F, not shifted", r2.get("F") == "42", r2.get("F"))
chk("omitted-H row: G (Цена) maps to G, not shifted into H's place", r2.get("G") == "5.50", r2.get("G"))
chk("omitted-H row: H is absent or blank, never '42' or '5.50'",
    r2.get("H", "") == "", r2.get("H"))

r3 = synthetic_rows[1]
chk("omitted-G-and-H row: F (Категория) still maps to F", r3.get("F") == "7", r3.get("F"))
chk("omitted-G-and-H row: G is absent or blank, not shifted from H's non-existence",
    r3.get("G", "") == "", r3.get("G"))

# Same synthetic data run through parse_lidl end to end: the H-omitted row is a
# non-promo product and must land only in regular_rows, never as an offer.
_orig_store_filter = C.LIDL_STORE_FILTER
C.LIDL_STORE_FILTER = "Пловдив"
synth_promo, synth_regular = sources.parse_lidl(synthetic_blob)
C.LIDL_STORE_FILTER = _orig_store_filter

chk("synthetic omitted-cell data: no promo offers (neither row has a promo price)",
    synth_promo == [], synth_promo)
chk("synthetic omitted-cell data: 1 regular row (only ТЕСТ001 has a Цена)",
    len(synth_regular) == 1, synth_regular)
if synth_regular:
    chk("synthetic omitted-cell data: regular price read from the correct column (5.50)",
        synth_regular[0]["price_eur"] == 5.50, synth_regular[0]["price_eur"])


# ── inline strings read correctly despite an EMPTY sharedStrings.xml ────────
chk("lidl fixture: a Cyrillic product name reads correctly (not blank) "
    "despite sharedStrings count=\"0\"",
    "Домати" in lidl_promo_by_name.get("Домати на клонка на кг", {}).get("name", ""),
    lidl_promo_by_name.get("Домати на клонка на кг"))


# ── de-dupe keeps the LOWEST promo price across stores ──────────────────────
DEDUPE_ROW_STORE_A = (
    '<row r="2">'
    '<c r="B2" t="inlineStr"><is><t>33333</t></is></c>'
    '<c r="C2" t="inlineStr"><is><t>100 - Пловдив/адрес А</t></is></c>'
    '<c r="D2" t="inlineStr"><is><t>Дедуп продукт</t></is></c>'
    '<c r="E2" t="inlineStr"><is><t>ДЕДУП01</t></is></c>'
    '<c r="F2" t="inlineStr"><is><t>3</t></is></c>'
    '<c r="G2" t="inlineStr"><is><t>4.00</t></is></c>'
    '<c r="H2" t="inlineStr"><is><t>2.50</t></is></c>'
    "</row>"
)
DEDUPE_ROW_STORE_B = (
    '<row r="3">'
    '<c r="B3" t="inlineStr"><is><t>44444</t></is></c>'
    '<c r="C3" t="inlineStr"><is><t>200 - Пловдив/адрес Б</t></is></c>'
    '<c r="D3" t="inlineStr"><is><t>Дедуп продукт</t></is></c>'
    '<c r="E3" t="inlineStr"><is><t>ДЕДУП01</t></is></c>'
    '<c r="F3" t="inlineStr"><is><t>3</t></is></c>'
    '<c r="G3" t="inlineStr"><is><t>4.00</t></is></c>'
    '<c r="H3" t="inlineStr"><is><t>1.99</t></is></c>'
    "</row>"
)
dedupe_blob = _build_xlsx([DEDUPE_ROW_STORE_A, DEDUPE_ROW_STORE_B])
C.LIDL_STORE_FILTER = "Пловдив"
dedupe_promo, dedupe_regular = sources.parse_lidl(dedupe_blob)
C.LIDL_STORE_FILTER = _orig_store_filter

chk("de-dupe: same product across 2 stores collapses to 1 promo offer",
    len(dedupe_promo) == 1, len(dedupe_promo))
if dedupe_promo:
    chk("de-dupe: keeps the LOWEST promo price (1.99, not 2.50)",
        dedupe_promo[0]["price_eur"] == 1.99, dedupe_promo[0]["price_eur"])


# ── _fetch_bytes monkeypatched to raise -> fetch_lidl returns ([], [], ok=False) ──

def _lidl_raiser(url, timeout=180):
    raise ConnectionError("lidl.bg unreachable")


sources._fetch_bytes = _lidl_raiser
lp, lr, lreport = sources.fetch_lidl()
chk("fetch_lidl with both URLs raising returns []", lp == [] and lr == [], (lp, lr))
chk("fetch_lidl with both URLs raising: ok is False", lreport["ok"] is False, lreport)
chk("fetch_lidl with both URLs raising: source is 'lidl'", lreport["source"] == "lidl", lreport)
sources._fetch_bytes = _orig_fetch_bytes


# ── one Lidl URL failing while the other succeeds still yields data ─────────

def _lidl_one_fails(url, timeout=180):
    if url == C.LIDL_EXPORT_URLS[0]:
        raise ConnectionError("first list down")
    return LIDL_FIXTURE


sources._fetch_bytes = _lidl_one_fails
lp2, lr2, lreport2 = sources.fetch_lidl()
chk("fetch_lidl: one URL failing still yields promo offers", len(lp2) == 26, len(lp2))
chk("fetch_lidl: one URL failing still yields regular rows", len(lr2) == 226, len(lr2))
chk("fetch_lidl: one URL failing -> ok stays True (partial success)", lreport2["ok"] is True, lreport2)
chk("fetch_lidl: one URL failing is noted", "first list down" in lreport2["note"], lreport2["note"])
sources._fetch_bytes = _orig_fetch_bytes


# ── below-threshold distinct-product count -> ok=True + warning ─────────────

sources._fetch_bytes = lambda url, timeout=180: LIDL_FIXTURE
lp3, lr3, lreport3 = sources.fetch_lidl()
chk("fetch_lidl below MIN_EXPECTED_OFFERS['lidl'] still ok=True", lreport3["ok"] is True, lreport3)
chk("fetch_lidl below MIN_EXPECTED_OFFERS['lidl'] warns in note",
    "WARNING" in lreport3["note"] and str(C.MIN_EXPECTED_OFFERS["lidl"]) in lreport3["note"],
    lreport3["note"])
sources._fetch_bytes = _orig_fetch_bytes


# ── harvest() with a healthy lidl source: promo joins all_offers, report joins
#    reports, regular_rows is the third element ──────────────────────────────

sources._fetch_text = _fake_fetch_text_both
sources._fetch_bytes = lambda url, timeout=180: LIDL_FIXTURE
all_offers_lidl, reports_lidl, regular_rows_lidl = sources.harvest()
sources._fetch_text = _orig_fetch_text2
sources._fetch_bytes = _orig_fetch_bytes

by_source_lidl = {r["source"]: r for r in reports_lidl}
chk("harvest with healthy lidl: all_offers includes ccc + mydealz + 26 lidl promo",
    len(all_offers_lidl) == 20 + 30 + 26, len(all_offers_lidl))
chk("harvest with healthy lidl: reports includes a lidl entry",
    "lidl" in by_source_lidl, by_source_lidl.keys())
chk("harvest with healthy lidl: regular_rows_lidl is the 226 distinct products",
    len(regular_rows_lidl) == 226, len(regular_rows_lidl))
chk("harvest with healthy lidl: lidl offers annotated (carry sku_class key)",
    all("sku_class" in o for o in all_offers_lidl if o["source"] == "lidl"))


# ── no broshura anywhere in sources.py ──────────────────────────────────────

sources_src = open("sources.py", encoding="utf-8").read()
chk('"broshura" does not appear in sources.py', "broshura" not in sources_src)


if _fails:
    print(f"\n{len(_fails)} failure(s): {_fails}")
else:
    print("\nAll source tests passed.")
sys.exit(1 if _fails else 0)
