"""
Stub verification for the Shop Hunter 8-stage pipeline.

Runs the real pipeline (find_deals.main()) in a throwaway temp directory with:
  - common.llm stubbed by response_schema identity (Stage 3 DISCOVER, Stage 4 AUDIT).
  - sources.harvest stubbed to return (offers, reports, regular_rows) directly, with the
    offers pre-annotated by match.annotate exactly as the real harvest() would.
  - common.send_email stubbed to capture the digest instead of sending it.

Fixtures (see OFFERS below):
  - Lidl salmon fillet promo at 9.80 EUR/kg vs a 12.00 EUR/kg par -> Fair (18% under, below
    the 20% Strong-Buy discount rung) -> exercises the exact
    "buy 5 kg = 49.00 EUR, saves 11.00 EUR" consumable line.
  - Lidl chicken breast promo at 9.00 EUR/kg vs a 6.00 EUR/kg par -> rejected `over_par` at
    Stage 2, before any LLM sees it -> exercises the reject footer's real EUR/kg-vs-par line.
  - Sony WH-1000XM5 at 179 EUR against its 200 EUR trigger -> Strong Buy outright, with NO
    reference price -> exercises the "no reference price" durable rendering and the
    HTML-escaping test (its audited value_case prose carries <script> and "Ben & Jerry's").
  - A generic washing machine at a claimed 40% off, fit_score 20 -> fails both the evidence
    and fit gates -> Skip, never emailed -> exercises "never shows in the digest".
  - An olive oil promo with an unparseable pack size -> the audit never recovers a usable
    unit price -> quarantined -> exercises "never enters price_history".
  - A Lidl REGULAR row for the same salmon sku -> recorded into the `regular` series with
    source="lidl_regular"; the salmon PROMO row above must never reach `regular`.
  - `mydealz` source report is forced to ok=False -> exercises the FAILED-source line.
  - `catalog_health.json` is seeded with runs_since_matched=7 for house.laundry_gel, which
    is not matched this run -> becomes stale (>=8) -> exercises the catalog-health line.

Run: python test_stub.py
"""

import json, os, re, sys, tempfile, shutil, datetime as dt

sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

_cwd = os.getcwd()
sandbox = tempfile.mkdtemp(prefix="sh_stub_")
os.makedirs(os.path.join(sandbox, "state"))
os.chdir(sandbox)

# Seed catalog_health so ONE known sku is one run away from CATALOG_STALE_RUNS (8) and
# will not be matched this run -> becomes stale and renders a catalog-health line.
with open("state/catalog_health.json", "w", encoding="utf-8") as f:
    json.dump({"skus": {"house.laundry_gel": {"runs_since_matched": 7, "last_matched": "2026-06-01"}}}, f)

# Seed price_history for the Par review block (find_deals.par_review_lines):
#   house.laundry_gel  par 4.50/L, REGULAR median 6.00/L over 30d, n=4 -> 33% gap -> a line.
#   food.coffee_beans  par 14.00/kg, PROMO median 28.00/kg, n=4 -> must produce NO line.
# The second half is the load-bearing one: a gap measured against PROMO prices is just the
# discount, and reporting it would send the user chasing their par downhill every week.
_D = [(60, ), (45, ), (30, ), (15, )]
with open("state/price_history.json", "w", encoding="utf-8") as f:
    def _obs(days_ago, price, source):
        d = (dt.date(2026, 7, 30) - dt.timedelta(days=days_ago)).isoformat()
        return {"d": d, "source": source, "unit_price_eur": price, "note": ""}
    json.dump({"skus": {
        "house.laundry_gel": {
            "unit": "L", "class": "consumable", "promo": [],
            "regular": [_obs(d, 6.00, "corroborate") for (d,) in _D],
            "stats": {"promo": {"n": 0, "min": None, "p10": None, "median": None, "last": None},
                      "regular": {"n": 4, "median": 6.00, "span_days": 45}},
        },
        "food.coffee_beans": {
            "unit": "kg", "class": "consumable",
            "promo": [_obs(d, 28.00, "lidl") for (d,) in _D],
            "regular": [],
            "stats": {"promo": {"n": 4, "min": 28.00, "p10": 28.00, "median": 28.00, "last": 28.00},
                      "regular": {"n": 0, "median": None, "span_days": 0}},
        },
        # par 4.80/kg vs a regular median of 4.90/kg -> a 2% gap, well inside
        # PAR_REVIEW_MIN_GAP. This sku exists to make that threshold load-bearing in the
        # test: without it, deleting the threshold check entirely still passed.
        "food.pork_meat": {
            "unit": "kg", "class": "consumable", "promo": [],
            "regular": [_obs(d, 4.90, "corroborate") for (d,) in _D],
            "stats": {"promo": {"n": 0, "min": None, "p10": None, "median": None, "last": None},
                      "regular": {"n": 4, "median": 4.90, "span_days": 45}},
        },
    }}, f)

import config as C
import common as X
import catalog
import match
import sources
import find_deals as fd

# ── Canned offers (as sources.harvest() would emit, pre-annotated) ──────────
OFFERS = [
    {"source": "lidl", "retailer": "Lidl", "name": "Сьомга филе 1 кг", "price_eur": 9.80,
     "was_price_eur": None, "claimed_discount": None, "valid_until": "2026-08-27",
     "url": "https://lidl.bg/salmon", "heat": None, "category_hint": None, "raw": ""},
    {"source": "lidl", "retailer": "Lidl", "name": "Пилешко филе 1 кг", "price_eur": 9.00,
     "was_price_eur": None, "claimed_discount": None, "valid_until": None,
     "url": "", "heat": None, "category_hint": None, "raw": ""},
    {"source": "ccc", "retailer": "Amazon.de", "name": "Sony WH-1000XM5", "price_eur": 179.0,
     "was_price_eur": 349.0, "claimed_discount": 0.4871, "valid_until": None,
     "url": "https://amazon.de/sony", "heat": None, "category_hint": None, "raw": ""},
    {"source": "mydealz", "retailer": "mydealz", "name": "Перална машина Bosch 7кг", "price_eur": 300.0,
     "was_price_eur": 500.0, "claimed_discount": 0.40, "valid_until": None,
     "url": "", "heat": 50, "category_hint": None, "raw": ""},
    {"source": "ccc", "retailer": "Amazon.de", "name": "Зехтин намаление", "price_eur": 9.99,
     "was_price_eur": 13.99, "claimed_discount": 0.2859, "valid_until": None,
     "url": "", "heat": None, "category_hint": None, "raw": ""},
]
for _o in OFFERS:
    match.annotate(_o)

REPORTS = [
    {"source": "ccc", "ok": True, "n": 2, "note": ""},
    {"source": "mydealz", "ok": False, "n": 0, "note": "HTTPError: 503 Service Unavailable"},
]

REGULAR_ROWS = [
    {"name": "Сьомга филе 1 кг", "product_code": "12345", "price_eur": 11.50, "category": "Месо и риба"},
]


def _stub_harvest():
    return OFFERS, REPORTS, REGULAR_ROWS


_AUDIT_BY_SKU = {
    "food.salmon_fillet": {
        "reference_price_eur": 12.0, "ref_confidence": "high",
        "ref_comparators": "matches the household's own par", "trap_detected": "none",
        "fit_score": 88, "quality_flag": "ok",
        "the_math": "18% under par; not yet at the 20% Strong-Buy rung.",
        "about": "Norwegian salmon fillet.", "value_case": "Solid freezer stock-up at this price.",
        "market_insight": "Deeper dips appear around Orthodox fasting periods.",
        "bulk_advice": "Portion into 400 g bags and freeze.", "red_flags": "none",
    },
    "av.sony_xm5": {
        "reference_price_eur": None, "ref_confidence": "low",
        "ref_comparators": "no credible current comparator found", "trap_detected": "none",
        "fit_score": 60, "quality_flag": "ok",
        "the_math": "Below the household's own pre-committed trigger price.",
        "about": "Sony's flagship noise-cancelling headphones.",
        "value_case": "Ben & Jerry's <script>alert(1)</script> good a deal as it gets at this price.",
        "market_insight": "Rarely dips below 220 EUR outside sale events.",
        "bulk_advice": "One unit only.", "red_flags": "Confirm the warranty region before buying.",
    },
    "house.washing_machine": {
        "reference_price_eur": 500.0, "ref_confidence": "medium",
        "ref_comparators": "matches the retailer's own claimed was-price, unverified elsewhere",
        "trap_detected": "none", "fit_score": 20, "quality_flag": "ok",
        "the_math": "The existing machine works fine; low household value from a new one.",
        "about": "A generic 7 kg washing machine.",
        "value_case": "Not worth it; nothing is broken.",
        "market_insight": "", "bulk_advice": "n/a", "red_flags": "none",
    },
    "food.olive_oil": {
        # Deliberately NO pack_qty/pack_unit -> pending_qty stays True -> quarantined.
        "reference_price_eur": 9.0, "ref_confidence": "low",
        "ref_comparators": "insufficient data", "trap_detected": "none",
        "fit_score": 50, "quality_flag": "ok",
        "the_math": "Pack size could not be confirmed from the listing.",
        "about": "Extra virgin olive oil.",
        "value_case": "Cannot judge value until the pack size is confirmed.",
        "market_insight": "", "bulk_advice": "",
        "red_flags": "Confirm pack size before buying.",
    },
}


def _extract_leads(prompt_text):
    m = re.search(r"### OFFERS TO AUDIT[^\n]*\n(\[[\s\S]*\])\n\n### OUTPUT FORMAT", prompt_text)
    if not m:
        return []
    return json.loads(m.group(1))


def _stub_llm(messages, model, max_tokens=2000, want_search=False, response_schema=None,
              provider=None, search_prompt=None):
    content = messages[0]["content"]
    if response_schema is C.STAGE_DISCOVER_SCHEMA:
        print("  [stub] llm: Stage 3 DISCOVER")
        return json.dumps({"offers": []})
    if response_schema is C.STAGE_AUDIT_SCHEMA:
        print("  [stub] llm: Stage 4 AUDIT")
        leads = _extract_leads(content)
        out = []
        for lead in leads:
            spec = _AUDIT_BY_SKU.get(lead.get("sku"))
            if spec is None:
                continue
            out.append({"lead_id": lead.get("lead_id"), **spec})
        return json.dumps(out)
    if response_schema is C.STAGE_CORROBORATE_SCHEMA:
        print("  [stub] llm: Stage 5 CORROBORATE")
        return json.dumps([])
    raise AssertionError(f"unexpected llm schema={response_schema}")


_email = {}


def _stub_send_email(subject, html_body, text_body):
    _email["subject"], _email["html"], _email["text"] = subject, html_body, text_body


sources.harvest = _stub_harvest
X.llm = _stub_llm
X.send_email = _stub_send_email

try:
    print("\n=== Running stub test (Shop Hunter 8-stage pipeline) ===\n")
    fd.main()

    print("\n=== Assertions ===")
    assert _email, "send_email was never called — no Strong Buy/Fair reached email"
    html_body, text_body = _email["html"], _email["text"]

    # Retailer sections appear in catalog.RETAILER_ORDER order (Lidl before Amazon.de).
    assert "Lidl" in html_body and "Amazon.de" in html_body, "expected Lidl and Amazon.de sections"
    assert html_body.index(">Lidl<") < html_body.index(">Amazon.de<"), \
        "retailer sections must follow catalog.RETAILER_ORDER (Lidl before Amazon.de)"
    print("Retailer sections ordered per catalog.RETAILER_ORDER [OK]")

    # Top-5 present and repeat-free (no seen state existed before this run).
    assert f"Top {C.TOP_N_BLOCK}" in html_body, "Top-N block header missing"
    assert "(repeat)" not in html_body, "first-ever run must have no repeats"
    print("Top-5 block present and repeat-free [OK]")

    # All three verdict badges render somewhere in the run log.
    run_md = open("state/run.md", encoding="utf-8").read()
    for badge in (C.VERDICT_LABEL[C.VERDICT_STRONG], C.VERDICT_LABEL[C.VERDICT_FAIR],
                  C.VERDICT_LABEL[C.VERDICT_SKIP]):
        assert badge in run_md, f"verdict badge {badge!r} missing from run.md"
    print("All three verdict badges rendered [OK]")

    # Exact consumable line: salmon at 9.80 EUR/kg, bulk_qty=5, par=12.00.
    assert "buy 5 kg = €49.00, saves €11.00" in html_body, \
        "exact consumable bulk-buy string missing"
    print("Exact consumable bulk-buy string rendered [OK]")

    # Reject footer carries a real EUR/kg-vs-par number (chicken breast, over_par).
    assert "over_par: €9.00/kg vs €6.00/kg par" in html_body, \
        "reject footer missing the real EUR/kg-vs-par number"
    print("Reject footer carries a real EUR/kg-vs-par number [OK]")

    # Source report includes the FAILED mydealz source.
    assert "mydealz" in html_body and "FAILED" in html_body, "FAILED source report missing"
    print("Source report includes a FAILED source [OK]")

    # Catalog health line for the seeded stale sku.
    assert "house.laundry_gel" in html_body and "no match in" in html_body, \
        "catalog-health line missing"
    print("Catalog health line present [OK]")

    # Off-list / spam guard: the low-fit washing machine must never reach Strong Buy —
    # it fails both evidence and fit gates and is never emailed at all.
    assert "Перална машина" not in html_body, "washing machine must not appear in the digest"
    print("Low-fit washing machine never reaches the digest [OK]")

    # Sony XM5: trigger hit -> Strong Buy with NO reference price, no NaN/None/EUR-None.
    assert "Sony WH-1000XM5" in html_body, "Sony XM5 missing from digest"
    sony_idx = html_body.index("Sony WH-1000XM5")
    badge_window = html_body[max(0, sony_idx - 300):sony_idx]
    assert C.VERDICT_LABEL[C.VERDICT_STRONG] in badge_window, "Sony XM5 should be a Strong Buy"
    assert "NaN" not in html_body and "€None" not in html_body, "unrendered null leaked into the email"
    print("Sony XM5 Strong Buy with no reference price, no NaN/None leakage [OK]")

    # HTML escaping: raw <script> must be absent; escaped forms must be present.
    assert "<script>alert(1)</script>" not in html_body, "raw <script> leaked into email HTML"
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_body, "escaped <script> missing"
    assert "Ben &amp; Jerry" in html_body, "escaped ampersand missing"
    print("HTML escaping applied to audited prose [OK]")

    # price_history.json: rejected chicken breast still recorded a promo observation.
    hist = json.load(open("state/price_history.json", encoding="utf-8"))
    chicken = hist["skus"].get("food.chicken_breast", {})
    assert (chicken.get("promo") or []), "rejected matched offer should still record a promo observation"
    print("Rejected matched offer recorded a promo observation [OK]")

    # Quarantined lead (olive oil, unparseable pack size) never enters price_history.
    assert "food.olive_oil" not in hist.get("skus", {}), \
        "quarantined lead must never enter price_history.json"
    print("Quarantined lead absent from price_history.json [OK]")

    # Lidl regular row recorded into `regular` with source=lidl_regular; the lidl PROMO
    # row for the same sku must never reach `regular`.
    salmon = hist["skus"]["food.salmon_fillet"]
    regular_sources = {r.get("source") for r in salmon.get("regular", [])}
    assert regular_sources == {"lidl_regular"}, f"expected only lidl_regular in regular series, got {regular_sources}"
    promo_sources = {p.get("source") for p in salmon.get("promo", [])}
    assert "lidl" in promo_sources, "the lidl promo row should still be in the promo series"
    print("Lidl regular row recorded distinctly from the lidl promo row [OK]")

    # deals_history.json: exactly the emailed set.
    n_strong = html_body.count(C.VERDICT_LABEL[C.VERDICT_STRONG])
    deals_hist = json.load(open("state/deals_history.json", encoding="utf-8"))
    assert len(deals_hist["entries"]) == 2, \
        f"expected 2 emailed deals (salmon Fair + Sony Strong), got {len(deals_hist['entries'])}"
    print("deals_history.json holds exactly the emailed set [OK]")

    # last_run.json: non-empty failed_gates histogram (calibration instrument).
    last_run = json.load(open("state/last_run.json", encoding="utf-8"))
    assert last_run.get("failed_gates"), "failed_gates histogram must not be empty"
    print("last_run.json failed_gates histogram non-empty [OK]")

    # Par review block: the visible half of effective_par's clamp. A par 33% away from the
    # observed REGULAR median must be reported to the human...
    assert "Par review" in html_body, "Par review block missing from the digest"
    assert re.search(r"house\.laundry_gel: your par is €4\.50/L, but the regular price has been "
                     r"€6\.00/L .*33% above your par", html_body), \
        "par-review line missing or malformed for the seeded regular-series gap"
    assert "Par review" in text_body and "house.laundry_gel: your par is" in text_body, \
        "par-review line missing from the text part"
    # ...but a gap measured against PROMO prices is just the discount, and must NOT appear.
    assert "food.coffee_beans" not in html_body, \
        "par review must read the regular series only — a promo-priced gap leaked in"
    # ...and a par that is merely 2% off is not news; reporting it would train the user to
    # ignore the block. This is what keeps PAR_REVIEW_MIN_GAP load-bearing.
    assert "food.pork_meat" not in html_body, \
        "par review fired on a sub-threshold gap — PAR_REVIEW_MIN_GAP is not being applied"
    print("Par review reports a regular-series gap, ignores promo gaps and sub-threshold gaps [OK]")

    print("\nAll assertions passed.")
finally:
    os.chdir(_cwd)
    shutil.rmtree(sandbox, ignore_errors=True)
