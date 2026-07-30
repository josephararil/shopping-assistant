"""Configuration for the Shop Hunter pipeline. Edit freely — knobs live HERE, nowhere else.

This file is the FROZEN CONTRACT for the whole pipeline. Field names, units, divisors,
verdict thresholds and the evidence model are authoritative here; every other module
binds to these names rather than restating a number.

Product data (the watchlist, the wishlist, retailer aliases, unit aliases) lives in
catalog.py, deliberately separated: it is ~400 lines of data with no logic, and mixing
it in here is how the travel repo's config.py grew to 727 lines of knobs+data+prompts.

── THE CANONICAL OFFER / CANDIDATE SHAPE ────────────────────────────────────────
sources.harvest() emits OFFER dicts. Every key is always present; unknown = None.

  source          str    "broshura" | "ccc" | "mydealz" | "llm_discover"
  retailer        str    display name, already normalised via catalog.RETAILER_ALIASES
  name            str    DISPLAY ONLY. Never a key, never matched by substring.
  price_eur       float  the offer price for the pack as sold. None if unparseable.
  was_price_eur   float  the retailer's own "before" claim, or None
  claimed_discount float 1 - price_eur/was_price_eur, or None. NOT evidence by itself.
  valid_until     str    "YYYY-MM-DD" or None
  url             str    or ""
  heat            int    mydealz community degrees, else None
  category_hint   str    a catalog.CATEGORY_HINTS key, or None
  raw             str    the source line/title, kept for debugging only

match.annotate() adds, in place:

  sku             str    catalog key, or "disc.<slug>" for off-list discovery, or None
  sku_class       str    "consumable" | "durable" | None
  match_conf      str    "high" | "medium" | None
  qty             float  pack size in the sku's unit, or None
  unit            str    "kg" | "L" | "pc"  — EXACTLY these three spellings
  unit_price_eur  float  price_eur / qty. Computed by PYTHON, never by an LLM.
  pending_qty     bool   True when sku matched but parse_qty failed; the audit
                         transcribes pack_qty/pack_unit and Python then divides.

prefilter() adds `reject_reason` (str) to rejects, and to every SURVIVOR adds:

  on_list         bool   True for a catalog sku, False for an off-list discovery find.
                         Passed straight to verdict_durable(on_list=...), which is why
                         it must be set here and nowhere else.

On an off-list discovery survivor it also MINTS `sku` = "disc." + match.slug(name),
`sku_class` = "durable" and `match_conf` = "medium". prefilter is the only place a
`disc.*` sku can come into existence — match.py never invents one, because minting an
identifier is a budgeting decision (it costs a Stage-4 slot), not a matching one.

The audit adds fit_score / reference_price_eur / trap_detected / prose fields.
Verdict stage adds verdict / discount / saving_eur / evidence / rank_score / failed_gates.
"""

import math
import os

# ── LLM models ──────────────────────────────────────────────────────────────
# Per-stage model roles. Values are canonical Anthropic model names; Gemini
# equivalents are looked up in GEMINI_MODEL_MAP below.
MODEL_DISCOVER    = "claude-sonnet-4-6"   # Stage 3: search-capable lead generation
MODEL_AUDIT       = "claude-sonnet-4-6"   # Stage 4: the procurement audit, no search
MODEL_CORROBORATE = "claude-sonnet-4-6"   # Stage 5: search-capable reference hunting

# Maps Anthropic model names (canonical keys) to Gemini equivalents.
# Used when LLM_PROVIDER=gemini. Add a new entry here whenever a new model role
# is added; never hard-code Gemini model names anywhere else.
GEMINI_MODEL_MAP = {
    "claude-haiku-4-5-20251001": "gemini-flash-latest",
    "claude-sonnet-4-6":         "gemini-pro-latest",
}

# Model that performs the live web-search grounding (google_search tool).
# Flagship models time out on Google's grounding gateway; the lite tier survives it.
# This is the only place the search model is named.
GEMINI_SEARCH_MODEL = "gemini-3.1-flash-lite"

# Optional per-stage provider overrides. None = use the global LLM_PROVIDER env var.
PROVIDER_DISCOVER    = None
PROVIDER_AUDIT       = None
PROVIDER_CORROBORATE = None

# ── LLM token budgets ────────────────────────────────────────────────────────
# IMPORTANT (Gemini thinking models): maxOutputTokens caps thinking tokens AND the
# visible answer combined. If the budget runs out mid-answer the JSON is truncated
# (finishReason=MAX_TOKENS) — which parses to nothing and looks like a quiet week.
# common._gemini warns on that; these budgets carry headroom above observed thinking.
MAX_TOKENS_DISCOVER    = 12000
MAX_TOKENS_AUDIT       = 16000   # ~200 tokens of prose per lead + a real reasoning pass
MAX_TOKENS_CORROBORATE = 12000

AUDIT_BATCH_SIZE       = 10      # leads per Stage-4 call
MAX_CORROBORATE_PER_RUN = 6      # Stage 5 is the only stage that scales with search latency

# Maximum web-search tool uses in a single call (Anthropic only; Gemini has no cap).
WEB_SEARCH_MAX_USES = 6

# ── Cost governor (Stage 2 prefilter) ────────────────────────────────────────
# These caps make LLM cost independent of how many offers the sources return: a feed
# could triple overnight and the bill would not move.
#
# WHY THERE IS NO `broshura` SOURCE — do not re-add one without re-verifying first.
# The plan specified broshura.bg/oferti as ~1552 product-level offers carrying name +
# EUR + BGN + retailer + "Важи до". That does not reproduce. Measured 2026-07-30:
# a plain GET of /oferti returns 218 KB containing FIVE EUR amounts in total, and
# /xhr/popularGridOffers, /oferti?page=2 and /hranitelni-stoki all return the same SPA
# shell. Rendered in a real browser with JS executed, the page shows brochure tiles
# (title + validity date, NO prices) plus an "Избрани продукти" widget of ~12-27
# FURNITURE items with EUR/BGN but no retailer and no validity date. The browser's own
# network trace shows only /xhr/geo, /xhr/tracking and /xhr/onsiteNotifications — there
# is no offer-data endpoint to call. What the site really serves is the scanned-image
# brochure listing the plan itself ruled out as OCR-only and out of scope.
#
# Consequence: consumables have no deterministic source, so Stage 3 DISCOVER is now
# their PRIMARY source rather than a gap-filler, and its cap carries most of the budget.
SOURCE_CAPS = {
    "lidl":         20,
    "ccc":           8,
    "mydealz":       8,
    "llm_discover": 30,
}

# Cap applied to a source with no SOURCE_CAPS entry. Deliberately non-zero: capping an
# unknown source at 0 silently discards every one of its offers, so adding a fetcher to
# sources.py and forgetting it here would produce a permanently empty feed that looks
# exactly like a quiet week. prefilter prints a loud warning when it falls back to this.
DEFAULT_SOURCE_CAP = 8

# A matched consumable whose unit price is plainly not a deal costs ZERO LLM tokens:
# rejected as `over_par` and rendered in the email's reject footer with real numbers.
# Slack above par so a borderline item still gets audited.
PREFILTER_PAR_SLACK = 1.15

# Durables cannot be prefiltered on price (the normal price is not known yet), only on
# CLAIMED discount steepness. This is what stops "washing machine 10% off" before it
# costs a token.
DURABLE_MIN_CLAIMED_DISCOUNT = 0.30
DURABLE_MIN_PRICE_EUR        = 30     # a 60% drop on a €9 cable is not news

# Off-list discovery (durables only). Off-list CONSUMABLE discovery is deliberately
# cut: with no par there is nothing to compute €/kg against.
DISCOVERY_MIN_CLAIMED_DISCOUNT = 0.50
DISCOVERY_MIN_PRICE_EUR        = 60

# Cap-filling order weights: mydealz heat earns a place in the ORDERING, never in the
# verdict. A community-vetted deal deserves a look; it does not deserve a tier.
HEAT_ORDER_DIVISOR = 300.0   # heat/300, capped at +0.5 on the ordering multiplier
HEAT_ORDER_MAX     = 0.5

# Stage 3: skus with zero matches from the deterministic feeds this run. With no leaflet
# source, that is effectively the WHOLE consumable watchlist every week, so this is sized
# to cover it rather than to sample it. Partial coverage is expected and correct — the
# prompt tells the model to OMIT an item it cannot price honestly, and catalog_health
# surfaces any sku that stays unmatched for CATALOG_STALE_RUNS runs.
MAX_GAP_QUERIES = 40

# Warn when a parse "succeeds" but returns implausibly little — the failure mode where
# a layout change silently drops 99% of offers and the week just looks quiet.
# ccc is set at 15 rather than 20 because the live feed carries exactly 20 items, so a
# threshold of 20 would fire on a single malformed title.
MIN_EXPECTED_OFFERS = {"lidl": 300, "ccc": 15, "mydealz": 10}

# `regular` observations may come ONLY from these sources, and this MUST stay an
# allowlist. Almost every source is a PROMOTIONS feed, so a price from one is a promo
# price by construction — a par blended from promo prices walks downhill weekly until the
# digest goes silently empty, which is failure mode #1 and it fails invisibly.
#
# Two sources qualify, for the same reason: each supplies a price that is genuinely NOT a
# promotion.
#   corroborate   — Stage-5 comparator listings at other retailers.
#   lidl_regular  — the `Цена` column of Lidl BG's legally-mandated daily price export,
#                   which is the ordinary shelf price. `Цена в промоция` is the promo and
#                   is harvested separately as source "lidl"; the two must never be mixed.
REGULAR_ALLOWED_SOURCES = {"corroborate", "lidl_regular"}

# ── Lidl BG statutory price export ──────────────────────────────────────────
# Bulgarian euro-adoption law (Закон за въвеждане на еврото, чл. 55б) requires retailers
# to publish daily per-store retail AND promotional prices. Lidl BG does so as .xlsx,
# which is a ZIP of XML and therefore parseable with stdlib zipfile + ElementTree — NO
# openpyxl and no new dependency. Verified 2026-07-30: HTTP 200, 6.76 MB, 102,097 rows,
# 709 unique products across 144 stores, prices in EUR, and www.lidl.bg/robots.txt does
# not disallow this path.
#
# This is the most valuable source in the pipeline and not because of its promotions: the
# `Цена` column is a genuine non-promo shelf price, which is the ONLY thing that can
# populate the `regular` series. Without it, effective_par and the regular_median evidence
# leg stay dead until the corroborate stage has slowly built ~12 weeks of history.
LIDL_EXPORT_URLS = [
    "https://www.lidl.bg/explore/assets/webPriceData/bg/ExportFirstList.xlsx",
    "https://www.lidl.bg/explore/assets/webPriceData/bg/ExportSecondList.xlsx",
]

# Only rows whose "Търговски обект" contains this string are used. The export is
# per-store and the household shops near Plovdiv, so a chain-wide average would be
# actively misleading — and store-level prices retire the "leaflet prices are chain-level,
# check your local store" caveat entirely. 11 Plovdiv stores, ~7,800 rows.
# Set to "" to take every store.
LIDL_STORE_FILTER = "Пловдив"

# The export is ~6.8 MB per list. Fetching two of them weekly is fine on CI, but a
# generous timeout is needed — the download reset once during testing at the default.
LIDL_HTTP_TIMEOUT = 180

# ── The evidence model ──────────────────────────────────────────────────────
# ref_evidence scores REFERENCE credibility, NEVER offer credibility. Offer
# credibility is near-constant across these feeds; the "before" price is where
# marketing nonsense lives. So the only question this model answers is: how much do
# we trust the number the discount is measured AGAINST?
#
# Lowering MIN_EVIDENCE_* is how this becomes a spam email. Tune the discount rungs
# instead.
EVIDENCE_WEIGHTS = {
    # A consumable's reference is the user's own hand-set par_eur. That is the most
    # credible reference in the system — the user pre-committed to it — so it carries
    # full weight. Without this leg no consumable could ever clear MIN_EVIDENCE_FAIR
    # from a leaflet (whose only other leg is retailer_claim at 0.2), and the entire
    # consumable half of the digest would sit at Fair for ~12 weeks while looking
    # like correct ruthless behaviour. Granted ONLY when a hand-set par exists.
    "user_par":       1.0,
    # The retailer's own "was" claim (leaflet strikethrough, Technopolis "-30%").
    # Deliberately near-worthless: alone, or with any ONE other weak leg, it cannot
    # reach MIN_EVIDENCE_STRONG. That is the whole answer to an inflated baseline.
    "retailer_claim": 0.2,
    # camelcamelcamel's `from W€` is its PREVIOUSLY TRACKED price, not a 90-day
    # average, and the pages holding real history are 403. Low trust by construction.
    "ccc_was":        0.3,
    # mydealz community heat above MYDEALZ_HOT_DEGREES — independent human vetting,
    # but of the deal, not of the reference. Weak on purpose.
    "mydealz_hot":    0.3,
    # Our own observed `regular` (non-promo) median with enough depth and span.
    # This is the statistic the whole price_history design exists to build.
    "regular_median": 1.0,
    # Stage-5 corroboration found >= CORROBORATE_MIN_LISTINGS independent current
    # listings. The only path by which a genuine off-list find reaches Strong Buy.
    "corroborated":   1.0,
}

MIN_EVIDENCE_FAIR            = 1.0
MIN_EVIDENCE_STRONG          = 2.0
MIN_EVIDENCE_STRONG_OFFLIST  = 2.5

MYDEALZ_HOT_DEGREES     = 300   # community heat that earns the mydealz_hot leg
REGULAR_MIN_N           = 4     # `regular` observations needed for the regular_median leg
REGULAR_MIN_SPAN_DAYS   = 21    # ... spread over at least this many days
CORROBORATE_MIN_LISTINGS = 2    # independent current listings for the corroborated leg


def ref_evidence(legs):
    """Total reference-credibility score from a set of leg names.

    `legs` is an iterable of EVIDENCE_WEIGHTS keys that are PRESENT for this lead.
    Unknown names are ignored (never raise — a typo must not silently inflate a score,
    and must not crash a weekly run either; it contributes 0.0 and is visible in the
    ledger's evidence_legs list).

    trap_detected from the audit is handled by the CALLER, which drops the
    "retailer_claim" leg before calling this. The LLM never vetoes; Python consumes
    the reported observation."""
    return round(sum(EVIDENCE_WEIGHTS.get(leg, 0.0) for leg in set(legs or ())), 3)


# ── Price history / par drift ───────────────────────────────────────────────
MAX_OBS_PER_SKU   = 40    # newest N observations kept per sku, per series
HISTORY_MAX_DAYS  = 540   # TTL for observations on catalog skus
DISC_SKU_MAX_DAYS = 90    # provisional `disc.*` skus prune fast so name-drift junk
                          # does not accumulate a phantom history

# promo_floor is the p10 of the PROMO series — the consumable ruthlessness lever.
# Salmon at €11.98 when the promo floor is €9.80 is not exceptional even though it
# beats the €12 par.
PROMO_FLOOR_PERCENTILE = 0.10
PROMO_FLOOR_MIN_N      = 4     # below this, promo_floor is None and near_floor is
                               # automatically satisfied. With 1-3 observations p10 is
                               # just "the cheapest thing we happened to see", and
                               # gating Strong Buy on it would suppress at random.

# effective_par PROPOSES, never overwrites. The user's hand-set par is authoritative;
# the pipeline may nudge it by at most this fraction, and only from the REGULAR series.
# Removing this clamp reintroduces silent par erosion — failure mode #1.
PAR_DRIFT_MAX = 0.15

# A sustained gap between the user's par and the regular median surfaces as a
# "par review" line in the email — a human decision, never an automatic one.
PAR_REVIEW_MIN_GAP = 0.20


def percentile(values, pct):
    """Nearest-rank percentile of an unsorted numeric list. Returns None when empty.

    Pinned here rather than inlined because history.py computes it and test_history.py
    asserts against it; two implementations of "p10" would silently disagree.
    Nearest-rank: index = ceil(pct * n) - 1, clamped. For n=12, pct=0.10 -> index 1
    (the second-smallest), which is the intended "a bit below the cheap end"."""
    vals = sorted(v for v in (values or []) if isinstance(v, (int, float)))
    if not vals:
        return None
    k = max(0, min(len(vals) - 1, math.ceil(pct * len(vals)) - 1))
    return vals[k]


def promo_floor(stats):
    """p10 of a sku's promo unit-price series, or None when the series is too thin.

    `stats` is price_history's per-sku stats dict: {"promo": {"n", "p10", ...}, ...}.
    Reads the precomputed p10 (recomputed by history.record_observation on every
    write) so the floor is identical everywhere it is consulted."""
    promo = (stats or {}).get("promo") or {}
    if (promo.get("n") or 0) < PROMO_FLOOR_MIN_N:
        return None
    return promo.get("p10")


def effective_par(sku_cfg, stats):
    """The par the verdict actually uses. PROPOSES, never overwrites.

    The user's hand-set par_eur is authoritative. The pipeline may nudge it toward the
    observed REGULAR median by at most PAR_DRIFT_MAX, and ONLY from the regular series
    (never from promo prices — every lead here is a promo by construction, so a par
    blended from them walks downhill weekly until the pipeline goes silent, invisibly).

    Returns (par_eur, drift) where drift is the applied fraction (0.0 when no usable
    regular evidence). Returns (None, 0.0) when the sku has no hand-set par."""
    par = (sku_cfg or {}).get("par_eur")
    if not isinstance(par, (int, float)) or par <= 0:
        return None, 0.0
    reg = (stats or {}).get("regular") or {}
    median = reg.get("median")
    if (not isinstance(median, (int, float)) or median <= 0
            or (reg.get("n") or 0) < REGULAR_MIN_N
            or (reg.get("span_days") or 0) < REGULAR_MIN_SPAN_DAYS):
        return par, 0.0
    lo, hi = par * (1 - PAR_DRIFT_MAX), par * (1 + PAR_DRIFT_MAX)
    clamped = max(lo, min(hi, median))
    return round(clamped, 4), round(clamped / par - 1, 4)


# ── Verdict thresholds ──────────────────────────────────────────────────────
# Verdict vocabulary is the user's own words. Three values, exactly these spellings.
VERDICT_STRONG = "Strong Buy"
VERDICT_FAIR   = "Fair"
VERDICT_SKIP   = "Skip"

VERDICT_RANK  = {VERDICT_STRONG: 0, VERDICT_FAIR: 1, VERDICT_SKIP: 2}
VERDICT_COLOR = {VERDICT_STRONG: "#0a7d2e", VERDICT_FAIR: "#8a6d00", VERDICT_SKIP: "#777"}
VERDICT_LABEL = {VERDICT_STRONG: "✅ Strong Buy", VERDICT_FAIR: "◾ Fair", VERDICT_SKIP: "· Skip"}
# CSS class suffix used by web/src/index.css and App.jsx. Keep in sync with both.
VERDICT_SLUG  = {VERDICT_STRONG: "strong", VERDICT_FAIR: "fair", VERDICT_SKIP: "skip"}

# Consumables: beat the par AND be near the historical promo floor. Beating a par that
# the market beats every second week is not news.
CONSUMABLE_STRONG_DISCOUNT = 0.20
CONSUMABLE_FAIR_DISCOUNT   = 0.05
CONSUMABLE_STRONG_MIN_FIT  = 70
PROMO_FLOOR_SLACK          = 1.10   # "near the floor" = within 10% above it

# Durables: the trigger price wins outright — the user pre-committed to an absolute
# number, so no amount of baseline inflation can fake it. Everything else must clear
# discount AND absolute saving AND evidence AND fit, and off-list needs more of all.
DURABLE_STRONG_DISCOUNT          = 0.35
DURABLE_STRONG_DISCOUNT_OFFLIST  = 0.45
DURABLE_FAIR_DISCOUNT            = 0.20
DURABLE_MIN_ABS_SAVING_EUR       = 40
DURABLE_STRONG_MIN_FIT           = 70
DURABLE_STRONG_MIN_FIT_OFFLIST   = 82

# Off-list discovery has a HARD Fair ceiling — this is the spam vector, so it is closed
# in code, not by tuning. Enforced in verdict_durable via on_list=False plus this flag.
OFFLIST_FAIR_CEILING = True

# rank_score makes a €11 salmon saving and a €170 headphone saving comparable so the
# Top-5 block can mix classes.
RANK_DISCOUNT_WEIGHT  = 40
RANK_DISCOUNT_FULL    = 0.50    # discount at which the discount term saturates
RANK_SAVING_WEIGHT    = 40
RANK_SAVING_FULL_EUR  = 150     # saving at which the saving term saturates
RANK_VERDICT_BONUS    = 20
RANK_REPEAT_PENALTY   = 15


def verdict_consumable(unit_price_eur, par_eur, floor, fit_score, evidence):
    """Beat the par AND be near the historical promo floor.

    Returns (verdict, discount, failed_gates). `failed_gates` is the list of gate names
    that blocked a Strong Buy — the calibration instrument. Read the failed_gates
    histogram in state/last_run.json BEFORE touching any threshold:
      discount dominates   -> CONSUMABLE_STRONG_DISCOUNT is too high
      evidence dominates   -> corroboration is under-firing; RAISE
                              MAX_CORROBORATE_PER_RUN, do NOT lower the evidence bar
      near_floor dominates -> pars are set above what the market routinely does
      fit dominates        -> the watchlist holds items the household does not want
    """
    if unit_price_eur is None or not par_eur:
        return VERDICT_SKIP, 0.0, ["no_price_or_par"]
    discount = 1 - unit_price_eur / par_eur
    near_floor = floor is None or unit_price_eur <= floor * PROMO_FLOOR_SLACK
    failed = []
    if discount < CONSUMABLE_STRONG_DISCOUNT:
        failed.append("discount")
    if not near_floor:
        failed.append("near_floor")
    if (fit_score or 0) < CONSUMABLE_STRONG_MIN_FIT:
        failed.append("fit")
    if (evidence or 0) < MIN_EVIDENCE_FAIR:
        failed.append("evidence")
    if not failed:
        return VERDICT_STRONG, discount, []
    if discount >= CONSUMABLE_FAIR_DISCOUNT:
        return VERDICT_FAIR, discount, failed
    return VERDICT_SKIP, discount, failed


def verdict_durable(price_eur, trigger_eur, ref_eur, evidence, fit_score, on_list):
    """Trigger price wins outright; everything else must clear four gates at once.

    Returns (verdict, discount, failed_gates). `discount` is None when no reference
    price is known — callers must treat that as 0 for ranking (rank_score does)."""
    if trigger_eur and price_eur is not None and price_eur <= trigger_eur:
        # The user pre-committed to this absolute number. No baseline inflation can
        # fake it, so no evidence, fit or discount requirement applies.
        return VERDICT_STRONG, (1 - price_eur / ref_eur if ref_eur else None), []
    if not ref_eur or price_eur is None:
        return VERDICT_SKIP, None, ["no_reference"]
    discount, saving = 1 - price_eur / ref_eur, ref_eur - price_eur
    min_disc = DURABLE_STRONG_DISCOUNT if on_list else DURABLE_STRONG_DISCOUNT_OFFLIST
    min_ev   = MIN_EVIDENCE_STRONG     if on_list else MIN_EVIDENCE_STRONG_OFFLIST
    min_fit  = DURABLE_STRONG_MIN_FIT  if on_list else DURABLE_STRONG_MIN_FIT_OFFLIST
    failed = []
    if discount < min_disc:
        failed.append("discount")
    if saving < DURABLE_MIN_ABS_SAVING_EUR:
        failed.append("abs_savings")
    if (evidence or 0) < min_ev:
        failed.append("evidence")
    if (fit_score or 0) < min_fit:
        failed.append("fit")
    if not on_list and OFFLIST_FAIR_CEILING:
        # Hard ceiling, in code rather than in a threshold: off-list discovery reaching
        # Strong Buy is THE spam vector. A tuning mistake must not be able to open it.
        failed.append("offlist_ceiling")
    if not failed:
        return VERDICT_STRONG, discount, []
    if discount >= DURABLE_FAIR_DISCOUNT and (evidence or 0) >= MIN_EVIDENCE_FAIR:
        return VERDICT_FAIR, discount, failed
    return VERDICT_SKIP, discount, failed


def rank_score(discount, saving_eur, verdict, is_repeat=False):
    """Cross-class ranking for the Top-5 block.

    `discount` and `saving_eur` may legitimately be None — verdict_durable returns
    discount=None on a trigger hit with no known reference, which is exactly the
    flagship Sony XM5 case. Both are coerced to 0 rather than crashing the run."""
    return round(
        RANK_DISCOUNT_WEIGHT * min(1.0, max(0.0, (discount or 0)) / RANK_DISCOUNT_FULL)
        + RANK_SAVING_WEIGHT * min(1.0, max(0.0, (saving_eur or 0)) / RANK_SAVING_FULL_EUR)
        + RANK_VERDICT_BONUS * (1 if verdict == VERDICT_STRONG else 0)
        - RANK_REPEAT_PENALTY * (1 if is_repeat else 0), 2)


def saving_eur_for(cand):
    """The real money moved by acting on this lead.

    Consumables: (par - unit_price) * bulk_qty — what "buy 5 kg and freeze" actually
    saves, which is the number the user reasons with. Durables: ref - price.
    Returns None when the inputs to make it honest are missing."""
    if cand.get("sku_class") == "consumable":
        par  = cand.get("par_eur")
        unit = cand.get("unit_price_eur")
        bulk = cand.get("bulk_qty")
        if par and unit is not None and bulk:
            return round((par - unit) * bulk, 2)
        return None
    ref, price = cand.get("reference_price_eur"), cand.get("price_eur")
    if ref and price is not None:
        return round(ref - price, 2)
    return None


# ── Anti-spam ───────────────────────────────────────────────────────────────
# The TTL is the ITEM'S OWN restock_days, not a global constant. A recurring Kaufland
# salmon promo the household already stocked up on stays quiet for ~90 days; a
# genuinely annual whey-protein promo re-alerts at ~300. One global TTL cannot do both,
# so prune_seen MUST prune per-record against that record's own ttl_days — pruning
# against one global cutoff would delete a 300-day whey suppression after 30 days.
DEFAULT_RESTOCK_DAYS = 60

# Price buckets are multiplicative and ~5% wide, so a genuine further drop is a new
# signal but ordinary noise is not.
PRICE_BUCKET_PCT = 0.05

# A materially better price overrides suppression regardless of remaining TTL.
PRICE_BREAKTHROUGH = 0.15


def price_bucket(cand):
    """~5%-wide multiplicative bucket index for the anti-spam key.

    Consumables bucket on unit_price_eur (€/kg is what the household compares);
    durables on price_eur (the ticket is the decision). Returns 0 for a missing or
    non-positive price so a broken lead cannot collide with a real one's bucket."""
    price = (cand.get("unit_price_eur") if cand.get("sku_class") == "consumable"
             else cand.get("price_eur"))
    if not isinstance(price, (int, float)) or price <= 0:
        return 0
    return int(math.floor(math.log(price) / math.log(1 + PRICE_BUCKET_PCT)))


def seen_key(cand):
    """sku | price_bucket.

    Retailer is stored IN the record, not in the key: suppression is about the
    HOUSEHOLD'S STOCK, not the shop — five chains carrying one stocked-up item would
    otherwise be five alerts. Keyed on sku, never on prose: the travel repo's
    _location() substring-matched free-text labels and silently applied the wrong par
    to every Bulgarian deal. Catalog slugs are permanent identifiers — renaming one
    resets that product's history and TTL."""
    return f"{cand.get('sku')}|{price_bucket(cand)}"


# ── Retention ───────────────────────────────────────────────────────────────
LEDGER_MAX_ENTRIES = 1500
LEDGER_MAX_DAYS    = 540
DEALS_HISTORY_MAX_ENTRIES = 3000
DEALS_HISTORY_MAX_DAYS    = 540
MAX_PROMPT_SKUS     = 12   # skus injected into a prompt's {memory} block
MAX_PROMPT_OUTCOMES = 10   # recent ledger outcomes injected into {memory}

# ── Email ───────────────────────────────────────────────────────────────────
TOP_N_BLOCK       = 5    # "Top 5 of the week", repeat-free, across retailers+classes
MAX_REJECT_LINES  = 40   # one-line reject footer, then a count for the rest
MAX_OFFLIST_LINES = 8    # off-list discovery block, badged, Fair ceiling
CATALOG_STALE_RUNS = 8   # runs_since_matched at which a sku is surfaced as a bad rule

# Email only when strong_buy + fair >= this. Otherwise write state and exit.
MIN_ITEMS_TO_EMAIL = 1

# ── Flags ───────────────────────────────────────────────────────────────────
# Runs EVERY stage and writes state/, prints the full breakdown per lead, but sends NO
# email and does NOT mark_seen(). Essential for weeks 1-4: the constants above are
# calibrated from a funnel estimate, and the failed_gates histogram is what turns them
# into constants calibrated from data.
DRY_RUN = os.environ.get("SHOP_HUNTER_DRY_RUN", "").strip() == "1"

# Skus that bypass suppression for one run, for debugging a single item.
FORCE_INCLUDE = set()


def gates_prompt_text():
    """Render the live gate thresholds for injection into a prompt.

    Every threshold reaches a prompt through THIS function, never as prose. The travel
    repo hardcoded ">= 80" in FIND_PROMPT while the gate lived in STAGE1_MIN_SCORE;
    they drifted and the prompt confidently lied to the model for months."""
    return (
        f"- A consumable reaches Strong Buy only at >= {round(CONSUMABLE_STRONG_DISCOUNT * 100)}% "
        f"under the household's target unit price, AND near its historical promo floor "
        f"(within {round((PROMO_FLOOR_SLACK - 1) * 100)}%), AND fit_score >= {CONSUMABLE_STRONG_MIN_FIT}.\n"
        f"- A wishlist durable at or below its trigger price is a Strong Buy outright.\n"
        f"- Any other durable needs >= {round(DURABLE_STRONG_DISCOUNT * 100)}% off a CREDIBLE "
        f"reference, >= EUR {DURABLE_MIN_ABS_SAVING_EUR} absolute saving, and fit_score "
        f">= {DURABLE_STRONG_MIN_FIT}.\n"
        f"- An off-list find can never exceed Fair, however steep the claimed discount.\n"
        f"- A retailer's own 'was' price is worth {EVIDENCE_WEIGHTS['retailer_claim']} of the "
        f"{MIN_EVIDENCE_STRONG} reference-credibility needed for a Strong Buy. It is nearly "
        f"worthless on its own — which is why an honest reference_price_eur matters more than "
        f"a big-looking discount."
    )


# ── Gemini search/reasoning split (see common._gemini) ───────────────────────
# On Gemini, want_search calls run in two steps. The search step runs on the lite model
# with google_search; SEARCH_RESULTS_PREAMBLE frames its output for the flagship
# reasoner, which has no live search tool. Applied via .replace (not .format) so leads
# containing braces cannot break it.
SEARCH_RESULTS_PREAMBLE = """### LIVE SEARCH RESULTS (a web search was run for you moments ago)
A separate scout already ran live web searches on your behalf and gathered the findings below. You do NOT have a live search tool in this step, so wherever the task text says "search the web", read it as: draw on these findings plus your own knowledge.

Treat them as a valuable fresh signal from the live internet — data you would not otherwise have. But they are a SEED, not a boundary. If the findings are thin or empty, do NOT give up and do NOT return an empty answer for that reason — reason your best from what you know, and be honest about what you could not confirm.

FINDINGS:
{leads}

--- END OF LIVE SEARCH RESULTS ---

Now complete the task below, using these findings as fresh input alongside your own reasoning:

"""

# The household context every prompt shares, verbatim from the user's requirements.
HOUSEHOLD_CONTEXT = """Household: a family of 3 (2 adults + a 4-year-old) living near Plovdiv, Bulgaria. Frugal, high-savings, buys in bulk and freezes. Quality over brand name — the best overall VALUE (quality per euro) is always preferred over the cheapest option and over the most prestigious one.
Currency: ALWAYS EUR. Never BGN, never lev, never "лв" — not in any field, not in any prose.
Shipping: assume delivery to Bulgaria is free and already included in every price you see."""


# ── Stage 3 · DISCOVER (search) ─────────────────────────────────────────────
# Leads, not verdicts — recall over precision. The deterministic prefilter and the
# audit cut hard downstream, so cast a wide net here.
DISCOVER_PROMPT = """Today is {today}. You are a sharp Bulgarian retail scout running live web searches. Your ONLY job in this step is to surface CONCRETE, CURRENT product offers as raw material for an auditor who works downstream. You are NOT deciding what is a good deal.

{household}

### WHAT TO FIND

1. **This is the household's shopping list. Finding this week's real price for these items IS your job** — not a side task. For each one, find the best current price at any Bulgarian retailer (Kaufland, Lidl, Billa, Metro, Fantastico, T MARKET) or a reputable online shop delivering to Bulgaria. Work through as many as you can:
{gap_skus}

2. **Always check these, every week:**
{always_check}

Cover breadth over depth: one honest price for each of twenty items is worth far more to us than four sources for one item. If you cannot find a real current price for an item, skip it and move on — we track which items keep coming back empty and fix our own search terms.

### WHAT MAKES A USABLE LEAD
- A NAMED product with a CONCRETE price in EUR and a NAMED retailer. "Metro has good meat prices" is useless; "Metro Bulgaria, Norwegian salmon fillet, 12.90 EUR/kg, valid to 12 Aug" is a lead.
- The PACK SIZE, as printed: "1 kg", "500 g", "2 x 0.5 L", "4 pcs". Report it verbatim in pack_qty/pack_unit. Do NOT compute a price per kilo or per litre — the pipeline does all arithmetic, and a division you do by hand becomes a fabricated discount nobody can trace.
- It is live NOW, or valid into the future relative to {today}. Expired promotions are worthless.
- Variety beats repetition. Assume last week you already reported the obvious ones; find different ones today.

### PRIOR RUNS (what we have already seen — do not just repeat it)
{memory}

### HONESTY RULES — READ THESE
- If you cannot find a real current price for one of the gap items, OMIT it. An invented price is far worse than a missing one: it enters a price history that shapes every future verdict for that product.
- Never guess a price from a product you did not actually see listed. Never convert from BGN and present it as a found EUR price without saying so in `evidence`.
- `sku` MUST be copied exactly from the list above. A sku we do not recognise is discarded silently, so a typo wastes the whole lead.
- `evidence` is where you say what you actually saw and where: the retailer page, the leaflet, the date. One honest sentence.

### OUTPUT FORMAT
Return JSON only. No markdown fences.

{{
  "offers": [
    {{
      "retailer": "Metro Bulgaria",
      "name": "Norwegian salmon fillet, fresh",
      "price_eur": 12.90,
      "pack_qty": 1,
      "pack_unit": "kg",
      "sku": "food.salmon_fillet",
      "valid_until": "2026-08-12",
      "url": "https://...",
      "evidence": "Metro BG online leaflet, week 32, seen {today}."
    }}
  ]
}}

If you found nothing real, return {{"offers": []}}. An empty list is a correct answer; a fabricated one is not."""


# ── Stage 4 · AUDIT (batched, no search) — the heart of it ───────────────────
AUDIT_PROMPT = """You are a Senior Logistics and Procurement Manager with 20+ years in European retail and supply chain, specialising in the Bulgarian market. You have deep expertise in FMCG pricing structures, promotional cycles (High-Low vs EDLP) and regional price benchmarking. Conduct a critical audit of the offers below. Strip away marketing bias. Be blunt when an offer is a marketing trap. Avoid sycophantic language.

Today is {today}.

{household}

### THE FOUR PILLARS OF YOUR AUDIT

**1. Price Benchmarking — is this a genuine discount, or inflated-then-slashed?**
Output `reference_price_eur` (what this item genuinely normally costs, per pack as sold), `ref_confidence`, `ref_comparators` (what you based it on), and `trap_detected`.

HONESTY IS CRITICAL on `reference_price_eur`: it directly gates the top verdict, so do NOT inflate it to manufacture a discount. If you cannot justify a higher normal price, set it equal to or below the offer price — that correctly yields no discount and no Strong Buy. A fabricated "normal" price is the one thing that will produce a false Strong Buy.

`trap_detected` is one of: `none`, `inflated_was_price` (the "before" price is not a price anything really sold at), `recurring_evergreen_promo` (this "special offer" is on more weeks than it is off, so the promo price IS the normal price), `shrinkflation` (the pack got smaller), `bundle_padding` (the discount only applies to a quantity nobody needs). This is a REPORTED OBSERVATION, not a veto — the pipeline consumes it by zeroing the retailer's-claim evidence leg. You do not kill offers.

**2. Unit Economics — TRANSCRIBE ONLY. DO NOT DIVIDE.**
Output `pack_qty` (a number) and `pack_unit` (one of exactly: `kg`, `g`, `L`, `ml`, `pcs`) exactly as printed on the offer. The pipeline computes every unit price and every discount itself.

Do NOT output a price per kilo, per litre or per piece. Do NOT reason about "per kilo" in any numeric field. If you read "100 g" and emit "1.09 EUR/kg", you manufacture a 91% discount that saturates the ranking, clears every discount rung and beats every floor — one arithmetic slip amplified by six gates. This is the single most important rule in this prompt.

**3. Contextual Fit — output `fit_score`, 0-100.**

**4. Logistics / Utility — output `bulk_advice` and `red_flags`.**
Shelf life, freezer and storage reality, whether the bulk quantity is genuinely usable before it spoils, and the opportunity cost of the cash and the space.

### HOW YOUR fit_score IS USED (read carefully — it changes how you should score)
`fit_score` measures NET HOUSEHOLD VALUE DELIVERED — how much this specific household genuinely gets out of owning this item. It is NOT prestige, NOT luxury, and NOT the size of the discount.

The pipeline applies the price and discount modifiers DETERMINISTICALLY, from the numbers you transcribe:

{gates}

Because the pipeline already turns price into the verdict, treat price as an input to your REASONING, not a scoring lever — do NOT also inflate or deflate fit_score for it. That double-counts, and double-counting price is how a cheap thing nobody needs becomes a Strong Buy.

You do not decide the verdict. You do not tier. You cannot kill an offer. Score honestly: an accurate 45 is far more useful to this pipeline than a nudged 80, because every score is recorded and the thresholds are calibrated from them.

### fit_score CALIBRATION (anchor to these)
- Salmon fillet at 9.80 EUR/kg against a 12.00 EUR/kg target, freezable, the family eats it weekly: **fit ~88**
- Branded olive oil at 7.20 EUR/L against a 9.00 EUR/L target, 2-year shelf life, everyday staple: **fit ~84**
- Whey protein at 14 EUR/kg against a 19 EUR/kg target, the annual promo, the user buys 50 kg: **fit ~92**
- Good coffee beans at a 25% discount, used daily, stores for months: **fit ~80**
- Premium ice cream at 30% off — freezer space is finite and this is not a staple: **fit ~45**
- A 12-pack of an energy drink nobody in the household drinks, 50% off: **fit ~12**
- A generic 300 L washing machine, 10% off, and the existing one works fine: **fit ~20**
- A 4th kitchen gadget that does one thing, 60% off: **fit ~15**

### `quality_flag` — the one lever you have
`quality_flag` is `ok` or `junk`. `junk` means the product itself is genuinely bad — a fake or misrepresented item, a dangerous one, or a brand that is a known quality disaster. It is ONE-WAY: it can demote a Strong Buy to Skip, and can never promote anything. It is not for "I don't think they need this" (that is fit_score) and not for "the discount looks fake" (that is trap_detected).

### PROSE FOR THE HUMAN (descriptive only)
`the_math` — actual versus perceived savings, in plain numbers, e.g. "The 30% off is calculated from a 4.99 EUR price this product has not sold at since spring; against the real 3.60 EUR shelf price you save 0.11 EUR, not 1.50 EUR."
`about` — what this product is, for someone who does not know the brand.
`value_case` — 1-3 sentences a savvy shopper would say out loud about whether this is worth buying.
`market_insight` — does this product see deeper discounts in a specific Bulgarian seasonal cycle? Worth waiting?

These four fields are DESCRIPTIVE ONLY. They must not move `fit_score` and they gate nothing. Do not invent named facts, certifications or origins you do not actually know.

### PRIOR RUNS (calibrate to these)
{memory}

### OFFERS TO AUDIT (each carries a `lead_id` you MUST echo back unchanged)
{leads}

### OUTPUT FORMAT
Return JSON only. No markdown fences. The root MUST be a bare JSON array starting with `[` — do NOT wrap it in an object. One object per input offer, in input order. Echo each `lead_id` unchanged. Do not invent, renumber or omit lead_ids.

[
  {{
    "lead_id": 1,
    "pack_qty": 0.5,
    "pack_unit": "kg",
    "reference_price_eur": 8.99,
    "ref_confidence": "high",
    "ref_comparators": "Kaufland and Billa both list this at 8.79-9.20 EUR outside promo weeks.",
    "trap_detected": "none",
    "fit_score": 88,
    "quality_flag": "ok",
    "the_math": "Perceived saving 40%; against the real 8.99 EUR normal price the saving is 2.10 EUR per pack, 22%.",
    "about": "Farmed Norwegian salmon, skin-on fillet portions.",
    "value_case": "At this price it is worth buying the freezer full; it has not been under 10 EUR/kg since January.",
    "market_insight": "Salmon discounts deepen around Orthodox fasting periods and pre-Christmas.",
    "bulk_advice": "Portion into 400 g bags and freeze; keeps 3 months.",
    "red_flags": "Check the packing date — this chain often promotes stock close to its sell-by."
  }}
]"""


# ── Stage 5 · CORROBORATE (search, gated, <= MAX_CORROBORATE_PER_RUN) ───────
CORROBORATE_PROMPT = """Today is {today}. You are a procurement analyst establishing the TRUE normal price of a small number of products, using live web search.

{household}

Each lead below cleared our value gates but lacks a credible reference price. Your findings decide whether it reaches the top verdict, so accuracy matters far more than helpfulness here.

### THE camelcamelcamel CAVEAT — READ THIS FIRST
Where a lead carries `was_price_eur` from camelcamelcamel, that figure is camelcamelcamel's PREVIOUSLY TRACKED price — NOT a 90-day average. It can easily be an inflated baseline from a brief price spike. Do not repeat it back to us as a reference. Establish the true normal price INDEPENDENTLY, from at least {min_listings} current listings at different retailers. If you cannot, set `corroborated: false` and put `reference_price_eur` at or below the offer price — that correctly yields no discount, which is the right answer when we do not know.

### RULES
- `listings` must be things you actually found: retailer, price in EUR, and a URL. Do not pad the list to reach {min_listings}. Two real listings beat five invented ones, and an invented listing corrupts this product's reference price for every future run.
- Never convert from BGN and present the result as a found EUR price without saying so in `red_flags`.
- `direction` is `lowers` if your reference is BELOW the reference we currently hold (the offer is less of a deal than it looked), `raises` if above, `confirms` if within 5%.
- A `lowers` finding is the most valuable output of this stage. Report it plainly. Do not soften it.

### LEADS
{leads}

### OUTPUT FORMAT
Return JSON only. No markdown fences. A bare JSON array, one object per lead, echoing `lead_id` unchanged.

[
  {{
    "lead_id": 1,
    "reference_price_eur": 289.00,
    "corroborated": true,
    "listings": [
      {{"retailer": "Technopolis", "price_eur": 299.00, "url": "https://..."}},
      {{"retailer": "Amazon.de", "price_eur": 279.00, "url": "https://..."}}
    ],
    "direction": "lowers",
    "evidence": "Two current listings at 279-299 EUR; the 349 EUR figure appears only in the retailer's own strikethrough.",
    "red_flags": "The Amazon.de price is a marketplace seller, not Amazon itself."
  }}
]"""


# ── Response schemas (Gemini responseSchema) ────────────────────────────────
# Gemini's responseSchema accepts only: type, properties, items, required, enum.
# No $schema, no description, no additionalProperties, no nullable.
# Keep in sync with the JSON examples in the prompts above.
# NOTE: these objects are matched by IDENTITY in test_stub.py's llm stub, so the
# pipeline must pass the module-level object itself, never a copy.

STAGE_DISCOVER_SCHEMA = {
    "type": "object",
    "properties": {
        "offers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "retailer":    {"type": "string"},
                    "name":        {"type": "string"},
                    "price_eur":   {"type": "number"},
                    "pack_qty":    {"type": "number"},
                    "pack_unit":   {"type": "string", "enum": ["kg", "g", "L", "ml", "pcs"]},
                    "sku":         {"type": "string"},
                    "valid_until": {"type": "string"},
                    "url":         {"type": "string"},
                    "evidence":    {"type": "string"},
                },
                "required": ["retailer", "name", "price_eur", "pack_qty", "pack_unit",
                             "sku", "evidence"],
            },
        },
    },
    "required": ["offers"],
}

STAGE_AUDIT_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "lead_id":             {"type": "integer"},
            "pack_qty":            {"type": "number"},
            "pack_unit":           {"type": "string", "enum": ["kg", "g", "L", "ml", "pcs"]},
            "reference_price_eur": {"type": "number"},
            "ref_confidence":      {"type": "string", "enum": ["high", "medium", "low"]},
            "ref_comparators":     {"type": "string"},
            "trap_detected":       {"type": "string",
                                    "enum": ["none", "inflated_was_price",
                                             "recurring_evergreen_promo", "shrinkflation",
                                             "bundle_padding"]},
            "fit_score":           {"type": "integer"},
            "quality_flag":        {"type": "string", "enum": ["ok", "junk"]},
            "the_math":            {"type": "string"},
            "about":               {"type": "string"},
            "value_case":          {"type": "string"},
            "market_insight":      {"type": "string"},
            "bulk_advice":         {"type": "string"},
            "red_flags":           {"type": "string"},
        },
        "required": ["lead_id", "reference_price_eur", "ref_confidence", "trap_detected",
                     "fit_score", "quality_flag", "the_math", "about", "value_case",
                     "bulk_advice", "red_flags"],
    },
}

STAGE_CORROBORATE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "lead_id":             {"type": "integer"},
            "reference_price_eur": {"type": "number"},
            "corroborated":        {"type": "boolean"},
            "listings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "retailer":  {"type": "string"},
                        "price_eur": {"type": "number"},
                        "url":       {"type": "string"},
                    },
                    "required": ["retailer", "price_eur"],
                },
            },
            "direction":  {"type": "string", "enum": ["lowers", "raises", "confirms"]},
            "evidence":   {"type": "string"},
            "red_flags":  {"type": "string"},
        },
        "required": ["lead_id", "reference_price_eur", "corroborated", "listings",
                     "direction", "evidence"],
    },
}
