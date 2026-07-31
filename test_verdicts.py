"""Truth table for config.py's verdict functions. No pytest, stdlib only, no I/O.

Pinned per the plan: every threshold in an assertion is DERIVED from a C.<CONSTANT>,
never retyped, so a future tuning change breaks this test instead of silently
disagreeing with it. Only test INPUT data (a price, a fit score) is a bare literal.
"""

import copy

import config as C
import catalog

_fails = []


def chk(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _fails.append(name)


# ── A single unverifiable "% off" must never be sufficient for Strong Buy ───
ev_alone = C.ref_evidence(["retailer_claim"])
ev_retailer_only = ev_alone  # same single-leg evidence, referenced again below
chk("retailer_claim alone < MIN_EVIDENCE_STRONG", ev_alone < C.MIN_EVIDENCE_STRONG,
    f"{ev_alone} vs {C.MIN_EVIDENCE_STRONG}")
for leg in C.EVIDENCE_WEIGHTS:
    if leg == "retailer_claim":
        continue
    total = C.ref_evidence(["retailer_claim", leg])
    chk(f"retailer_claim + {leg} < MIN_EVIDENCE_STRONG", total < C.MIN_EVIDENCE_STRONG,
        f"{total} vs {C.MIN_EVIDENCE_STRONG}")


# ── Consumables: judged against an OBSERVED reference, not a hand-set par ────
# REFERENCE is the Lidl shelf p25 for this sku. 12.00/kg keeps the arithmetic of the
# cases below identical to when this number was salmon's par_eur, so the discount rungs
# stay pinned to exactly the values they were calibrated at.
REFERENCE = 12.00
HIGH, LOW = C.CONF_HIGH, C.CONF_LOW
ev_shelf_retailer = C.ref_evidence(["statutory_shelf", "retailer_claim"])

v, discount, failed = C.verdict_consumable(7.20, REFERENCE, HIGH, None, 92, ev_shelf_retailer)
chk("consumable 40% under, no floor yet, fit 92: Strong Buy", v == C.VERDICT_STRONG, f"got {v}")

v, discount, failed = C.verdict_consumable(9.80, REFERENCE, HIGH, None, 92, ev_shelf_retailer)
chk("consumable 18% under (below Strong rung): Fair", v == C.VERDICT_FAIR, f"got {v}")

floor = 8.00
v, discount, failed = C.verdict_consumable(9.00, REFERENCE, HIGH, floor, 90, ev_shelf_retailer)
chk("consumable 25% under but above floor slack: Fair", v == C.VERDICT_FAIR, f"got {v}")
# Exact set, not just membership: 0.25 must CLEAR CONSUMABLE_STRONG_DISCOUNT on its own
# (only near_floor blocks it) — this is what actually pins the discount rung, since
# "near_floor in failed" alone would stay true even if the discount gate also failed.
chk("consumable 25% under: failed_gates is exactly ['near_floor']",
    failed == ["near_floor"], f"failed={failed}")
chk("9.00 is actually above floor*PROMO_FLOOR_SLACK",
    9.00 > floor * C.PROMO_FLOOR_SLACK, "test setup invalid")
chk("0.25 discount clears CONSUMABLE_STRONG_DISCOUNT on its own",
    (1 - 9.00 / REFERENCE) >= C.CONSUMABLE_STRONG_DISCOUNT, "test setup invalid")

v, discount, failed = C.verdict_consumable(8.50, REFERENCE, HIGH, floor, 90, ev_shelf_retailer)
chk("consumable 29% under, near floor, fit 90: Strong Buy", v == C.VERDICT_STRONG, f"got {v}")
chk("8.50 is actually within floor*PROMO_FLOOR_SLACK",
    8.50 <= floor * C.PROMO_FLOOR_SLACK, "test setup invalid")

v, discount, failed = C.verdict_consumable(8.50, REFERENCE, HIGH, floor, 60, ev_shelf_retailer)
chk("consumable same price, fit 60: Fair", v == C.VERDICT_FAIR, f"got {v}")
chk("consumable fit 60: 'fit' in failed_gates", "fit" in failed, f"failed={failed}")

v, discount, failed = C.verdict_consumable(11.90, REFERENCE, HIGH, None, 92, ev_shelf_retailer)
chk("consumable under 5%: Skip", v == C.VERDICT_SKIP, f"got {v}")

# The statutory_shelf leg is load-bearing exactly as user_par used to be: without a
# full-weight reference leg the 40%-under case falls to Fair, which is what would happen
# to EVERY leaflet consumable if the leg were ever removed.
v, discount, failed = C.verdict_consumable(7.20, REFERENCE, HIGH, None, 92, ev_retailer_only)
chk("consumable 40% under WITHOUT a shelf leg: Fair", v == C.VERDICT_FAIR, f"got {v}")
chk("consumable 40% under WITHOUT a shelf leg: 'evidence' in failed_gates",
    "evidence" in failed, f"failed={failed}")

# No reference of any kind -> Skip. Inventing a denominator is how a pipeline
# manufactures discounts, so the honest answer is to decline to judge.
v, discount, failed = C.verdict_consumable(7.20, None, None, None, 92, ev_shelf_retailer)
chk("no reference at all: Skip with ['no_reference']",
    (v, discount, failed) == (C.VERDICT_SKIP, 0.0, ["no_reference"]), (v, discount, failed))


# ── A LOW-confidence reference can never reach Strong Buy ───────────────
# Two cases share this ceiling: an L3 llm_reference (no LLM-supplied number is ever the
# authority on price) and a wide-spread L2 whose p25 averages across product grades that
# are not the same thing. It is enforced in CODE, not by a threshold, because a tuning
# mistake must not be able to open a spam vector.
v, discount, failed = C.verdict_consumable(7.20, REFERENCE, LOW, None, 92, ev_shelf_retailer)
chk("40% under on a LOW-confidence reference: Fair, never Strong",
    v == C.VERDICT_FAIR, f"got {v}")
chk("low confidence names itself in failed_gates",
    "low_confidence_reference" in failed, f"failed={failed}")
chk("...and it is the ONLY thing blocking it — the ceiling is doing the work",
    failed == ["low_confidence_reference"], f"failed={failed}")

# The ceiling must hold no matter how good the discount looks.
for price in (6.00, 3.00, 0.50):
    v, _d, _f = C.verdict_consumable(price, REFERENCE, LOW, None, 95,
                                     C.ref_evidence(["statutory_shelf", "corroborated"]))
    chk(f"low-confidence reference at {price:.2f} "
        f"({round((1 - price / REFERENCE) * 100)}% under) is still not Strong",
        v != C.VERDICT_STRONG, f"got {v}")


# ── target_eur is a PROMOTE-ONLY pre-commitment ──────────────────────
# The user named the number themselves, so no baseline inflation can fake it and no
# other gate applies. It is NEVER a denominator.
v, discount, failed = C.verdict_consumable(22.50, 30.00, HIGH, 20.00, 10, 0.0,
                                           target_eur=25.00)
chk("target_eur hit: Strong Buy despite failing floor, fit AND evidence",
    (v, failed) == (C.VERDICT_STRONG, []), (v, failed))
chk("target_eur hit reports the discount against the REFERENCE, not the target",
    abs(discount - (1 - 22.50 / 30.00)) < 1e-9, discount)

v, _d, _f = C.verdict_consumable(25.01, 30.00, HIGH, None, 10, 0.0, target_eur=25.00)
chk("a cent above target_eur does NOT get the free pass", v != C.VERDICT_STRONG, v)

v, discount, _f = C.verdict_consumable(22.50, None, HIGH, None, 10, 0.0, target_eur=25.00)
chk("target_eur works with no reference at all (discount 0.0, still Strong)",
    (v, discount) == (C.VERDICT_STRONG, 0.0), (v, discount))

# A LOW-confidence reference must not block a target hit — the pre-commitment is the
# user's own number and does not depend on our reference being trustworthy.
v, _d, _f = C.verdict_consumable(22.50, 30.00, LOW, None, 10, 0.0, target_eur=25.00)
chk("target_eur outranks the low-confidence ceiling too", v == C.VERDICT_STRONG, v)


# ── THE FOUR RUN-#5 REGRESSIONS ──────────────────────────────
# Every one of these was a WRONG verdict produced by a hardcoded par on 2026-07-31.
# They are the reason the par machinery was deleted, so they are named cases here.

# 1. A shampoo sku (house.shampoo, since removed from the catalog) at 3.79/L was a
#    Strong Buy against an 8.00 par. Lidl's own shelf shampoo was 2.89/L — a "52% off"
#    Amazon deal that costs MORE than just buying shampoo. The observed reference
#    inverts the sign of the discount. The sku is gone; the numbers being pinned here
#    (3.79 vs 2.89) are not.
v, discount, failed = C.verdict_consumable(3.79, 2.89, HIGH, None, 90, ev_shelf_retailer)
chk("regression: shampoo 3.79/L vs 2.89 shelf is NOT a Strong Buy",
    v != C.VERDICT_STRONG, f"got {v}")
chk("regression: shampoo's discount is NEGATIVE — dearer than the shelf", discount < 0, discount)

# 2. supp.whey_protein: 22.50/kg was a Skip under a guessed 19.00 par, despite being
#    under the user's real 25/kg bar. target_eur is exactly this case.
v, _d, failed = C.verdict_consumable(22.50, None, None, None, 50, 0.0, target_eur=25.00)
chk("regression: whey 22.50/kg with a 25.00 target is a Strong Buy",
    v == C.VERDICT_STRONG, f"got {v}")
whey_cfg = catalog.WATCHLIST["supp.whey_protein"]
chk("regression: the whey target_eur is actually set in the catalog",
    whey_cfg.get("target_eur") == 25.00, whey_cfg.get("target_eur"))

# 3. food.kashkaval: 19.375/kg cleared a 9.00 par's Fair rung. Against the observed
#    11.50 p25 it is 68% MORE expensive than the shelf.
v, discount, failed = C.verdict_consumable(19.375, 11.50, HIGH, None, 90, ev_shelf_retailer)
chk("regression: kashkaval 19.375/kg vs 11.50 p25 is a Skip", v == C.VERDICT_SKIP, f"got {v}")

# 4. food.beans_dried: rejected as over_par at a MIS-PARSED 16.90/kg. At its real price
#    against the observed shelf p25 it is the find of the week.
v, discount, failed = C.verdict_consumable(1.69, 2.30, HIGH, None, 90, ev_shelf_retailer)
chk("regression: beans 1.69/kg vs 2.30 p25 is a Strong Buy",
    v == C.VERDICT_STRONG, f"got {v} failed={failed}")
chk("regression: beans discount is ~26.5%", abs(discount - 0.2652) < 0.001, discount)


# ── quality_flag is ONE-WAY (property test via a local helper) ──────────────
def apply_quality_flag(verdict, flag):
    """Documented rule: junk demotes Strong Buy -> Skip; nothing else changes."""
    if flag == "junk" and verdict == C.VERDICT_STRONG:
        return C.VERDICT_SKIP
    return verdict


for verdict_in in (C.VERDICT_STRONG, C.VERDICT_FAIR, C.VERDICT_SKIP):
    for flag in ("ok", "junk"):
        out = apply_quality_flag(verdict_in, flag)
        chk(f"quality_flag({verdict_in!r}, {flag!r}) never promotes to Strong Buy",
            not (out == C.VERDICT_STRONG and verdict_in != C.VERDICT_STRONG),
            f"in={verdict_in} flag={flag} out={out}")


# ── rank_score: monotonic in saving_eur ──────────────────────────────────────
salmon_discount = 1 - 7.20 / 12.00
salmon_saving = (12.00 - 7.20) * 5.0   # bulk_qty=5
salmon_score = C.rank_score(salmon_discount, salmon_saving, C.VERDICT_STRONG)
salmon_score_bigger_saving = C.rank_score(salmon_discount, salmon_saving * 2, C.VERDICT_STRONG)
chk("rank_score: monotonic in saving_eur", salmon_score_bigger_saving >= salmon_score,
    f"salmon={salmon_score} bigger_saving={salmon_score_bigger_saving}")

base_score = C.rank_score(0.5, 100.0, C.VERDICT_STRONG, is_repeat=False)
repeat_score = C.rank_score(0.5, 100.0, C.VERDICT_STRONG, is_repeat=True)
chk("rank_score: repeat penalty is exactly RANK_REPEAT_PENALTY",
    round(base_score - repeat_score, 2) == C.RANK_REPEAT_PENALTY,
    f"base={base_score} repeat={repeat_score}")

_weird_values = [None, -1, -1e9, 0, 1e12]
_raised = False
for d in _weird_values:
    for s in _weird_values:
        for verdict_in in (C.VERDICT_STRONG, C.VERDICT_FAIR, C.VERDICT_SKIP):
            for rep in (True, False):
                try:
                    C.rank_score(d, s, verdict_in, is_repeat=rep)
                except Exception:
                    _raised = True
chk("rank_score never raises for weird discount/saving combinations", not _raised)


# ── price_bucket and seen_key ────────────────────────────────────────────────
cand_1pct_a = {"sku_class": "consumable", "unit_price_eur": 10.00}
cand_1pct_b = {"sku_class": "consumable", "unit_price_eur": 10.10}
chk("price_bucket: 1% apart share a bucket",
    C.price_bucket(cand_1pct_a) == C.price_bucket(cand_1pct_b),
    f"{C.price_bucket(cand_1pct_a)} vs {C.price_bucket(cand_1pct_b)}")

cand_20pct_a = {"sku_class": "consumable", "unit_price_eur": 10.00}
cand_20pct_b = {"sku_class": "consumable", "unit_price_eur": 12.00}
chk("price_bucket: 20% apart do NOT share a bucket",
    C.price_bucket(cand_20pct_a) != C.price_bucket(cand_20pct_b),
    f"{C.price_bucket(cand_20pct_a)} vs {C.price_bucket(cand_20pct_b)}")

cand_retailer_a = {"sku": "food.salmon_fillet", "sku_class": "consumable",
                    "unit_price_eur": 10.00, "retailer": "Kaufland"}
cand_retailer_b = {"sku": "food.salmon_fillet", "sku_class": "consumable",
                    "unit_price_eur": 10.00, "retailer": "Billa"}
chk("seen_key: same sku/price, different retailer -> SAME key",
    C.seen_key(cand_retailer_a) == C.seen_key(cand_retailer_b),
    f"{C.seen_key(cand_retailer_a)} vs {C.seen_key(cand_retailer_b)}")
chk("seen_key: retailer text is not present in the key",
    "Kaufland" not in C.seen_key(cand_retailer_a) and "Billa" not in C.seen_key(cand_retailer_b))


# ── reference_for: three levels, tried in order ──────────────────────
watchlist_before = copy.deepcopy(catalog.WATCHLIST)

WIDE   = {"n": 20, "p25": 5.00, "p10": 2.00, "p90": 8.00, "spread": 4.0}
NARROW = {"n": 20, "p25": 5.00, "p10": 4.00, "p90": 6.00, "spread": 1.5}
THIN   = {"n": 1,  "p25": 5.00, "p10": 5.00, "p90": 5.00, "spread": 1.0}
EMPTY  = {"n": 0,  "p25": None, "p10": None, "p90": None, "spread": None}

# L1 — the same product code's own statutory shelf price, from the same export row.
lidl = {"source": "lidl", "was_price_eur": 12.00, "qty": 2.0, "reference_price_eur": 99.0}
ref, level, conf = C.reference_for(lidl, NARROW)
chk("reference_for L1: a Lidl was_price becomes a UNIT price (12.00 / 2 kg)",
    (ref, level, conf) == (6.00, C.REF_OWN_SHELF, C.CONF_HIGH), (ref, level, conf))
chk("reference_for L1 outranks both a p25 and an LLM reference", ref == 6.00)

# L1 is Lidl-ONLY. Every source carries a was_price_eur, but only Lidl's is statutory;
# an Amazon "was" is a retailer claim, and letting a seller set its own denominator is
# the exact marketing nonsense this pipeline exists to reject.
for src in ("ccc", "mydealz", "llm_discover", None):
    other = {"source": src, "was_price_eur": 12.00, "qty": 2.0}
    ref, level, conf = C.reference_for(other, NARROW)
    chk(f"reference_for: source={src!r} was_price is NOT an own_shelf reference",
        level == C.REF_CATEGORY_P25, (ref, level))

# A Lidl row with no usable qty cannot produce a UNIT price, so it falls through.
ref, level, conf = C.reference_for(
    {"source": "lidl", "was_price_eur": 12.00, "qty": None}, NARROW)
chk("reference_for: a Lidl row with no qty falls through to L2",
    level == C.REF_CATEGORY_P25, (ref, level))

# L2 — p25 of the sku's observed shelf prices, confidence from the grade spread.
ref, level, conf = C.reference_for({"source": "ccc"}, NARROW)
chk("reference_for L2: narrow spread -> p25 at HIGH confidence",
    (ref, level, conf) == (5.00, C.REF_CATEGORY_P25, C.CONF_HIGH), (ref, level, conf))
ref, level, conf = C.reference_for({"source": "ccc"}, WIDE)
chk("reference_for L2: spread over BASELINE_MAX_SPREAD -> LOW confidence",
    (ref, level, conf) == (5.00, C.REF_CATEGORY_P25, C.CONF_LOW), (ref, level, conf))
chk("the WIDE/NARROW fixtures actually straddle the threshold",
    WIDE["spread"] > C.BASELINE_MAX_SPREAD and NARROW["spread"] <= C.BASELINE_MAX_SPREAD)

# A thin series is not a baseline. n below REGULAR_MIN_N must fall through, or one lucky
# observation becomes the authority on a whole sku's price.
ref, level, conf = C.reference_for({"source": "ccc", "reference_price_eur": 7.0}, THIN)
chk("reference_for: a series thinner than REGULAR_MIN_N falls through to L3",
    (ref, level, conf) == (7.0, C.REF_LLM, C.CONF_LOW), (ref, level, conf))
chk("the THIN fixture is actually below REGULAR_MIN_N", THIN["n"] < C.REGULAR_MIN_N)

# L3 — always LOW confidence, which is what caps it at Fair.
ref, level, conf = C.reference_for({"source": "ccc", "reference_price_eur": 7.0}, EMPTY)
chk("reference_for L3: an LLM reference is ALWAYS low confidence",
    (ref, level, conf) == (7.0, C.REF_LLM, C.CONF_LOW), (ref, level, conf))

# Nothing at all. The caller must Skip rather than invent a denominator.
chk("reference_for: no reference of any kind -> (None, None, None)",
    C.reference_for({"source": "ccc"}, EMPTY) == (None, None, None),
    C.reference_for({"source": "ccc"}, EMPTY))
chk("reference_for: a None baseline is survivable",
    C.reference_for({"source": "ccc"}, None) == (None, None, None))
chk("reference_for: non-positive values are never a reference",
    C.reference_for({"source": "ccc", "reference_price_eur": 0}, EMPTY) == (None, None, None))

chk("reference_for: catalog.WATCHLIST not mutated", catalog.WATCHLIST == watchlist_before)

# The par machinery is GONE, not renamed. Restoring it under a new name would
# reintroduce the silent par erosion this whole change exists to remove.
for gone in ("effective_par", "PAR_DRIFT_MAX", "PAR_REVIEW_MIN_GAP"):
    chk(f"config.{gone} no longer exists", not hasattr(C, gone))
_still_par = [k for k, v in catalog.CATALOG.items() if "par_eur" in v]
chk("no catalog item carries a par_eur any more", _still_par == [], _still_par[:5])
chk("the user_par evidence leg is gone", "user_par" not in C.EVIDENCE_WEIGHTS)
chk("the shelf legs replaced it at full weight",
    C.EVIDENCE_WEIGHTS["own_shelf"] == 1.0 and C.EVIDENCE_WEIGHTS["statutory_shelf"] == 1.0)
# The bar itself must NOT have moved — lowering it is how this becomes a spam email.
chk("MIN_EVIDENCE_FAIR is still 1.0", C.MIN_EVIDENCE_FAIR == 1.0)
chk("MIN_EVIDENCE_STRONG is still 2.0", C.MIN_EVIDENCE_STRONG == 2.0)


# ── gates_prompt_text must not drift from the constants ─────────────────────
text = C.gates_prompt_text()
expect_consumable_pct = f">= {round(C.CONSUMABLE_STRONG_DISCOUNT * 100)}%"
expect_evidence = f"{C.EVIDENCE_WEIGHTS['retailer_claim']} of the {C.MIN_EVIDENCE_STRONG} reference-credibility"

chk("gates_prompt_text contains CONSUMABLE_STRONG_DISCOUNT", expect_consumable_pct in text,
    f"looked for {expect_consumable_pct!r}")
chk("gates_prompt_text contains MIN_EVIDENCE_STRONG", expect_evidence in text,
    f"looked for {expect_evidence!r}")


print(f"\n{len(_fails)} failure(s)" if _fails else "\nAll verdict tests passed.")
import sys
sys.exit(1 if _fails else 0)
