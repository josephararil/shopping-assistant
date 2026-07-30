"""sources.py — deterministic feed fetchers for the Shop Hunter pipeline.

Two sources only: `ccc` (camelcamelcamel top price drops, RSS) and `mydealz` (mydealz
hot deals, RSS). A third grocery-leaflet source named in the original plan was
measured on 2026-07-30 and does not exist as a scrapeable feed (see the comment
above config.SOURCE_CAPS for the measurement) — it is NOT implemented here and must
never be added.

`_fetch_text` is the only function in this module that touches the network; every
parser below is pure and takes a string. Every `fetch_*` wraps fetch+parse in
try/except and NEVER raises — a failing source contributes [] plus a visible report
line (CONTRACT §9).
"""

import html
import re
import xml.etree.ElementTree as ET

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

    Distinct from match.parse_eur: that function's regex allows only ONE decimal
    separator group, so it mis-splits a thousands-separated figure like
    "1.393,28€" (it would return 393.28, silently dropping the leading "1."). The
    pepper:merchant `price` attribute is already an isolated, well-formed amount —
    no surrounding prose to search through — so it gets its own strip-and-parse
    rather than reusing parse_eur for a shape parse_eur was not built to handle.
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


def harvest():
    """-> (all_offers, reports). The assembler. NEVER raises."""
    all_offers = []
    reports = []

    for fetch_fn in (fetch_ccc, fetch_mydealz):
        offers, report = fetch_fn()
        all_offers.extend(offers)
        reports.append(report)

    for offer in all_offers:
        match.annotate(offer)

    return all_offers, reports
