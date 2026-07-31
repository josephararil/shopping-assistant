"""sources.py — deterministic feed fetchers for the Shop Hunter pipeline.

Three sources: `ccc` (camelcamelcamel top price drops, RSS), `mydealz` (mydealz hot
deals, RSS) and `lidl` (Lidl BG's statutory daily price export, .xlsx). A grocery-
leaflet source named in the original plan was measured on 2026-07-30 and does not
exist as a scrapeable feed (see the comment above config.SOURCE_CAPS for the
measurement) — it is NOT implemented here and must never be added.

`_fetch_text` / `_fetch_bytes` are the only functions in this module that touch the
network; every parser below is pure. Every `fetch_*` wraps fetch+parse in try/except
and NEVER raises — a failing source contributes [] plus a visible report line
(CONTRACT §9).
"""

import html
import re
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO

import config as C
import catalog
import match

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_CCC_URL = "https://de.camelcamelcamel.com/top_drops/feed"
_MYDEALZ_URL = "https://www.mydealz.de/rss/hot"

_PEPPER_NS = "{http://www.pepper.com/rss}"

_CCC_TITLE_RE = re.compile(
    r"^(?P<name>.+?) - down (?P<pct>[\d.]+)% \((?P<diff>[\d,]+)€\) "
    r"to (?P<now>[\d,]+)€ from (?P<was>[\d,]+)€$"
)
_HEAT_RE = re.compile(r"^(?P<heat>\d+)°\s*-\s*(?P<name>.*)$")


def _fetch_text(url, timeout=25):
    """The single HTTP chokepoint. RAISES on any failure. Nothing else does I/O."""
    import requests

    resp = requests.get(url, headers={"User-Agent": _UA}, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def _fetch_bytes(url, timeout=25):
    """The binary HTTP chokepoint (for the Lidl .xlsx export). RAISES on any failure."""
    import requests

    resp = requests.get(url, headers={"User-Agent": _UA}, timeout=timeout, stream=True)
    resp.raise_for_status()
    chunks = []
    for chunk in resp.iter_content(chunk_size=1 << 16):
        if chunk:
            chunks.append(chunk)
    return b"".join(chunks)


def _blank_offer(source, raw):
    """The 11 contract keys, unknown = None (url = "")."""
    return {
        "source": source,
        "retailer": None,
        "name": None,
        "price_eur": None,
        "was_price_eur": None,
        "claimed_discount": None,
        "valid_until": None,
        "url": "",
        "heat": None,
        "category_hint": None,
        "raw": raw,
    }


def _normalise_retailer(raw_retailer):
    if not raw_retailer:
        return None
    return catalog.RETAILER_ALIASES.get(raw_retailer.strip().lower(), raw_retailer.strip().title())


def _claimed_discount(price_eur, was_price_eur):
    if price_eur is None or was_price_eur is None or was_price_eur <= 0:
        return None
    return 1 - price_eur / was_price_eur


def _parse_de_amount(text):
    """European-formatted amount ('1.393,28€', '442€') -> float, or None.

    Still distinct from match.parse_eur, but no longer for the original reason: that
    function used to mis-split "1.393,28€" as 393.28 and now handles separators
    correctly. What remains is that parse_eur searches PROSE and therefore requires a
    currency marker ("€" or "eur") next to the digits, while the pepper:merchant
    `price` attribute is a bare, already-isolated amount with no marker at all, so it
    gets its own strip-and-parse. Note the two are NOT interchangeable in the other
    direction either: this function treats "." as a thousands separator, which would
    read Lidl's dot-decimal "7.15" as 715.
    """
    if not text:
        return None
    m = re.search(r"[\d.,]+", text)
    if not m:
        return None
    digits = m.group(0).replace(".", "").replace(",", ".")
    try:
        return float(digits)
    except ValueError:
        return None


def parse_ccc(xml_text):
    """-> list[offer]. Pure. Raises nothing on odd input."""
    offers = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return offers

    for item in root.findall(".//item"):
        title_el = item.find("title")
        if title_el is None or not title_el.text:
            continue
        title = html.unescape(title_el.text)
        m = _CCC_TITLE_RE.match(title)
        if not m:
            continue  # malformed title shape — skipped, never raises

        name = m.group("name").strip()
        now = float(m.group("now").replace(",", "."))
        was = float(m.group("was").replace(",", "."))

        offer = _blank_offer("ccc", title)
        offer["name"] = name
        offer["price_eur"] = now
        # `was` is CCC's own PREVIOUSLY TRACKED price, not a 90-day average — that is
        # why the evidence model (config.EVIDENCE_WEIGHTS ccc_was) weights it at only
        # 0.3 rather than treating it as a trustworthy reference price.
        offer["was_price_eur"] = was
        offer["claimed_discount"] = _claimed_discount(now, was)
        offer["retailer"] = _normalise_retailer("Amazon.de")
        offer["category_hint"] = None

        link_el = item.find("link")
        offer["url"] = link_el.text.strip() if link_el is not None and link_el.text else ""
        offer["valid_until"] = match.parse_valid_until(title)

        offers.append(offer)

    return offers


def parse_mydealz(xml_text):
    """-> list[offer]. Pure."""
    offers = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return offers

    for item in root.findall(".//item"):
        title_el = item.find("title")
        raw_title = html.unescape(title_el.text) if title_el is not None and title_el.text else ""

        heat_match = _HEAT_RE.match(raw_title) if raw_title else None
        if heat_match:
            heat = int(heat_match.group("heat"))
            name = heat_match.group("name").strip()
        else:
            heat = None
            name = raw_title.strip()

        merchant_el = item.find(f"{_PEPPER_NS}merchant")
        merchant_name = merchant_el.get("name") if merchant_el is not None else None
        merchant_price = merchant_el.get("price") if merchant_el is not None else None

        offer = _blank_offer("mydealz", raw_title)
        offer["name"] = name
        offer["heat"] = heat
        offer["price_eur"] = _parse_de_amount(merchant_price)
        offer["was_price_eur"] = None  # mydealz carries no trustworthy "before" figure
        offer["claimed_discount"] = _claimed_discount(offer["price_eur"], offer["was_price_eur"])
        offer["retailer"] = _normalise_retailer(merchant_name) if merchant_name else "mydealz"

        cat_el = item.find("category")
        cat_text = (cat_el.text or "").strip().lower() if cat_el is not None and cat_el.text else ""
        offer["category_hint"] = cat_text if cat_text in catalog.CATEGORY_HINTS else None

        link_el = item.find("link")
        offer["url"] = link_el.text.strip() if link_el is not None and link_el.text else ""
        offer["valid_until"] = match.parse_valid_until(raw_title)

        offers.append(offer)

    return offers


def _min_expected_note(source, n):
    minimum = C.MIN_EXPECTED_OFFERS.get(source)
    if minimum is not None and n < minimum:
        return f"WARNING: parsed only {n} offers, expected >= {minimum} — check for a layout change"
    return None


def fetch_ccc():
    """-> (offers, report). NEVER raises."""
    source = "ccc"
    try:
        xml_text = _fetch_text(_CCC_URL)
        offers = parse_ccc(xml_text)
        n = len(offers)
        note = _min_expected_note(source, n) or ""
        return offers, {"source": source, "ok": True, "n": n, "note": note}
    except Exception as e:
        return [], {"source": source, "ok": False, "n": 0, "note": f"{type(e).__name__}: {e}"}


def fetch_mydealz():
    """-> (offers, report). NEVER raises."""
    source = "mydealz"
    try:
        xml_text = _fetch_text(_MYDEALZ_URL)
        offers = parse_mydealz(xml_text)
        n = len(offers)
        note = _min_expected_note(source, n) or ""
        return offers, {"source": source, "ok": True, "n": n, "note": note}
    except Exception as e:
        return [], {"source": source, "ok": False, "n": 0, "note": f"{type(e).__name__}: {e}"}


_XLSX_ROW_RE = re.compile(r'<row[^>]*r="\d+"[^>]*>.*?</row>', re.DOTALL)
_XLSX_CELL_RE = re.compile(
    r'<c r="(?P<col>[A-Z]+)\d+"[^>]*?(?:/>|>(?P<body>.*?)</c>)', re.DOTALL
)
_XLSX_INLINE_TEXT_RE = re.compile(r"<is>\s*<t[^>]*>(?P<text>.*?)</t>\s*</is>", re.DOTALL)


def _parse_xlsx_sheet(blob):
    """-> (header_map, rows). Pure.

    Reads xl/worksheets/sheet1.xml out of the .xlsx zip by hand: stdlib zipfile + a
    regex cell scan rather than a full ET parse of the (huge) worksheet, because the
    only structure that matters is "which column letter does this <c> belong to and
    what inline text (if any) does it carry". Cells are t="inlineStr" with
    <is><t>...</t></is> — sharedStrings.xml is empty (count="0") for this export, so
    a shared-strings lookup would return every cell blank. Columns start at B and
    trailing empty cells are OMITTED from a row entirely, so cells are mapped by the
    column letter parsed out of each <c>'s own r="B23" attribute — never positionally.

    `header_map` maps the row-1 header TEXT (stripped) to its column letter — this is
    what makes column resolution survive Lidl shipping two files with completely
    different schemas under the same file format (see parse_lidl). `rows` is every
    data row (row 2+), still keyed by column letter — some callers only need that.
    """
    rows = []
    header_map = {}
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        sheet_xml = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")

    for row_idx, row_match in enumerate(_XLSX_ROW_RE.finditer(sheet_xml)):
        row_xml = row_match.group(0)
        row = {}
        for cell_match in _XLSX_CELL_RE.finditer(row_xml):
            col = cell_match.group("col")
            body = cell_match.group("body") or ""
            text_match = _XLSX_INLINE_TEXT_RE.search(body)
            row[col] = html.unescape(text_match.group("text")) if text_match else ""
        if row_idx == 0:
            header_map = {text.strip(): col for col, text in row.items() if text.strip()}
        else:
            rows.append(row)

    return header_map, rows


def _parse_xlsx_rows(blob):
    """-> list[dict] keyed by COLUMN LETTER ("B", "C", ...). Pure.

    Thin wrapper over `_parse_xlsx_sheet` for callers that only need the column-
    letter-keyed data rows (e.g. the omitted-trailing-cell guard). The header row is
    excluded here too.
    """
    return _parse_xlsx_sheet(blob)[1]


def _resolve_lidl_col(header_map, *names):
    """First matching header NAME's column letter, or raise. Exact match only —
    "Цена" must never substring-match "Цена в промоция" or "Референтната цена / цена
    на дребно", or this reintroduces the exact bug this function exists to prevent,
    just from the other direction (a loose match instead of a hard-coded letter)."""
    for name in names:
        if name in header_map:
            return header_map[name]
    raise ValueError(
        f"Lidl export is missing an expected column (tried {list(names)!r}); "
        f"header row had: {sorted(header_map)!r}"
    )


def _parse_net_qty(text):
    """Lidl's `Нетно количество` cell ('1.00000', '0,40000') -> float, or None.

    A bare unitless decimal: the manufacturer's statutory net-quantity declaration,
    which is a MASS or VOLUME and never a piece count. The unit is implied by the
    product, so `match.annotate` only trusts it where `match.unit_of(sku)` is kg or L
    — see that function for why a per-piece sku must not use it.

    Deliberately not `_parse_de_amount` (which treats "." as a thousands separator and
    would read "1.00000" as 100000) and not `match.parse_eur` (which needs a currency
    marker). Both separators are accepted as DECIMAL here because the column carries no
    thousands grouping — a net quantity in the thousands does not occur in a grocery
    export, so there is no ambiguity to resolve.
    """
    s = (text or "").strip().replace(",", ".")
    if not s:
        return None
    try:
        value = float(s)
    except ValueError:
        return None
    # A zero or negative net quantity is a data error, not a quantity. Letting it
    # through would divide a price by ~0 and manufacture an enormous unit price.
    return value if value > 0 else None


def parse_lidl(blob):
    """-> (promo_offers, regular_rows). Pure.

    Lidl ships its two export files under COMPLETELY DIFFERENT schemas — same file
    format, different column meanings entirely (e.g. the first file's column E is
    "Код на продукта"/product code, the second's column E is "Марка"/brand, almost
    always empty). Columns are therefore resolved by HEADER NAME via `header_map`,
    never by a hard-coded letter — a hard-coded map silently reads one file's
    category-number column as the other file's promo price. If a required column
    name is absent, this raises (never falls back to a guessed letter); `fetch_lidl`
    turns that into a per-URL failure note instead of silently injecting garbage into
    the promo/regular series.

    promo_offers: one canonical offer per row with a non-empty promo price,
    de-duped across stores by product code keeping the LOWEST promo price.
    regular_rows: one per distinct product code, the LOWEST regular/shelf price
    observed — never an offer, never mixed with the promo series.

    Both carry `net_qty`: the statutory net-quantity declaration, verbatim, or None
    when the file's schema has no such column (the first file's does not).
    """
    header_map, rows = _parse_xlsx_sheet(blob)

    col_store = _resolve_lidl_col(header_map, "Търговски обект", "Данни за търговския обект")
    col_name = _resolve_lidl_col(header_map, "Наименование на продукта", "Име на продукта")
    col_code = _resolve_lidl_col(header_map, "Код на продукта")
    col_category = _resolve_lidl_col(header_map, "Категория")
    col_regular = _resolve_lidl_col(header_map, "Цена", "Референтната цена / цена на дребно")
    col_promo = _resolve_lidl_col(header_map, "Цена в промоция", "Текущата намалена цена")
    # Only the second file's schema carries a real promo validity date; the first
    # file's schema has no such column at all, so this stays None for its rows.
    col_valid_until = header_map.get("Срок на намаление до")
    # Same story for the manufacturer's statutory net-quantity declaration: present as
    # column F of the second schema and populated on every row, absent entirely from
    # the first. Resolved with .get(), NEVER _resolve_lidl_col — a file that legitimately
    # has no net quantity must degrade to the name-parsing path, not fail the source.
    col_net_qty = header_map.get("Нетно количество")

    if C.LIDL_STORE_FILTER:
        rows = [r for r in rows if C.LIDL_STORE_FILTER in r.get(col_store, "")]

    best_promo = {}  # code -> (promo_price, name, category, regular_price, valid_until, net_qty)
    best_regular = {}  # product_code -> (regular_price, name, category, net_qty)

    for row in rows:
        code = row.get(col_code, "").strip()
        if not code:
            continue
        name = row.get(col_name, "").strip()
        category = row.get(col_category, "").strip() or None
        regular_text = row.get(col_regular, "").strip()
        promo_text = row.get(col_promo, "").strip()

        valid_until = None
        if col_valid_until:
            raw_date = row.get(col_valid_until, "").strip()
            if raw_date:
                valid_until = match.parse_valid_until(raw_date)

        net_qty = _parse_net_qty(row.get(col_net_qty, "")) if col_net_qty else None

        # Never use the source's own "Процентното изменение между двете цени" column
        # (M in the second schema) — Python computes claimed_discount itself; a
        # source's own arithmetic is never trusted (CONTRACT: the LLM never performs
        # arithmetic, and neither does a feed).
        regular_price = match.parse_eur(regular_text + " €") if regular_text else None
        if regular_price is not None:
            prev = best_regular.get(code)
            if prev is None or regular_price < prev[0]:
                best_regular[code] = (regular_price, name, category, net_qty)

        if promo_text:
            promo_price = match.parse_eur(promo_text + " €")
            if promo_price is not None:
                prev = best_promo.get(code)
                if prev is None or promo_price < prev[0]:
                    best_promo[code] = (
                        promo_price, name, category, regular_price, valid_until, net_qty,
                    )

    promo_offers = []
    for code, (promo_price, name, category, was_price, valid_until, net_qty) in best_promo.items():
        # `raw` is a STRING for every source — it reaches prose contexts (the audit
        # leads block, run.md, html.escape) where a dict would raise. The product code
        # is what de-duping keys on, so it gets its own field rather than riding inside
        # `raw`: an extra key is additive, a type change is not.
        offer = _blank_offer("lidl", f"{code} — {name}")
        offer["product_code"] = code
        offer["net_qty"] = net_qty
        offer["retailer"] = "Lidl"
        offer["name"] = name
        offer["price_eur"] = promo_price
        offer["was_price_eur"] = was_price
        offer["claimed_discount"] = _claimed_discount(promo_price, was_price)
        offer["valid_until"] = valid_until
        offer["url"] = ""
        offer["heat"] = None
        offer["category_hint"] = None
        promo_offers.append(offer)

    regular_rows = [
        {"name": name, "product_code": code, "price_eur": price, "category": category,
         "net_qty": net_qty}
        for code, (price, name, category, net_qty) in best_regular.items()
    ]

    return promo_offers, regular_rows


def fetch_lidl():
    """-> (promo_offers, regular_rows, report). NEVER raises.

    Fetches BOTH C.LIDL_EXPORT_URLS and merges; one URL failing while the other
    succeeds still yields data plus a note recording the failure.
    """
    source = "lidl"
    promo_offers = []
    regular_rows = []
    notes = []
    any_ok = False

    for url in C.LIDL_EXPORT_URLS:
        try:
            blob = _fetch_bytes(url, timeout=C.LIDL_HTTP_TIMEOUT)
            offers, regulars = parse_lidl(blob)
            promo_offers.extend(offers)
            regular_rows.extend(regulars)
            any_ok = True
        except Exception as e:
            notes.append(f"{url}: {type(e).__name__}: {e}")

    if not any_ok:
        return [], [], {
            "source": source, "ok": False, "n": 0, "note": "; ".join(notes) or "all URLs failed",
        }

    # De-dupe across the two lists the same way as across stores: lowest promo price
    # / lowest regular price per product code wins.
    #
    # ...but a losing row's `net_qty` is CARRIED FORWARD onto the winner. The two files
    # list the same products at identical prices under the same product codes (measured
    # 2026-07-31: 700/700 shared, every price equal), and only the second schema
    # declares a net quantity. Price-only de-duping therefore keeps the first file's row
    # and silently discards the statutory quantity for EVERY product — 0 of 700 rows
    # carried a net_qty before this backfill. It fails invisibly: each file parses
    # perfectly on its own, so nothing testing a single file can see it. The price rule
    # itself is untouched; only a field the winner does not have is filled in.
    def _merge(rows_in):
        merged = {}
        for row in rows_in:
            code = row["product_code"]
            prev = merged.get(code)
            if prev is None:
                merged[code] = row
                continue
            winner, loser = (row, prev) if row["price_eur"] < prev["price_eur"] else (prev, row)
            if winner.get("net_qty") is None and loser.get("net_qty") is not None:
                winner["net_qty"] = loser["net_qty"]
            merged[code] = winner
        return list(merged.values())

    promo_offers = _merge(promo_offers)
    regular_rows = _merge(regular_rows)

    distinct_products = len({r["product_code"] for r in regular_rows} | {o["product_code"] for o in promo_offers})
    all_notes = notes + ([_min_expected_note(source, distinct_products)] if _min_expected_note(source, distinct_products) else [])
    note = "; ".join(all_notes)
    return promo_offers, regular_rows, {
        "source": source, "ok": True, "n": distinct_products, "note": note,
    }


def harvest():
    """-> (all_offers, reports, regular_rows). The assembler. NEVER raises."""
    all_offers = []
    reports = []

    for fetch_fn in (fetch_ccc, fetch_mydealz):
        offers, report = fetch_fn()
        all_offers.extend(offers)
        reports.append(report)

    lidl_promo, regular_rows, lidl_report = fetch_lidl()
    all_offers.extend(lidl_promo)
    reports.append(lidl_report)

    for offer in all_offers:
        match.annotate(offer)

    return all_offers, reports, regular_rows
