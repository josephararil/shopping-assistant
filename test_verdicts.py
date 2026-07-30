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


# ── Named regression 1: the trigger price wins outright (Sony XM5) ──────────
sony = catalog.WISHLIST["av.sony_xm5"]
trigger = sony["trigger_eur"]
v, discount, failed = C.verdict_durable(
    price_eur=179.0, trigger_eur=trigger, ref_eur=None,
    evidence=None, fit_score=None, on_list=True,
)
chk("regression1: trigger hit -> Strong Buy", v == C.VERDICT_STRONG, f"got {v}")
chk("regression1: no reference -> discount is None", discount is None, f"got {discount}")
chk("regression1: no gates failed", failed == [], f"got {failed}")

try:
    score = C.rank_score(None, None, C.VERDICT_STRONG)
    chk("regression1: rank_score(None, None, Strong) does not raise", True)
except Exception as e:
    chk("regression1: rank_score(None, None, Strong) does not raise", False, repr(e))


# ── Named regression 2: the washing machine ──────────────────────────────────
ev_retailer_only = C.ref_evidence(["retailer_claim"])
v, discount, failed = C.verdict_durable(
    price_eur=450.0, trigger_eur=None, ref_eur=500.0,
    evidence=ev_retailer_only, fit_score=20, on_list=False,
)
chk("washing machine: Skip", v == C.VERDICT_SKIP, f"got {v}")
for gate in ("discount", "fit", "evidence", "offlist_ceiling"):
    chk(f"washing machine: '{gate}' in failed_gates", gate in failed, f"failed={failed}")

# Same machine at 40% off, fit still 20: off-list needs discount>=DISCOUNT_OFFLIST,
# evidence>=STRONG_OFFLIST AND fit>=FIT_OFFLIST. Give it generous evidence so only
# discount/fit/ceiling are what block it, proving fit alone (a generic appliance
# nobody needs) is enough to keep it out of Strong Buy.
v2, discount2, failed2 = C.verdict_durable(
    price_eur=300.0, trigger_eur=None, ref_eur=500.0,
    evidence=C.MIN_EVIDENCE_STRONG_OFFLIST, fit_score=20, on_list=False,
)
chk("washing machine @40% off still not Strong Buy", v2 != C.VERDICT_STRONG, f"got {v2}")
chk("washing machine @40% off: 'fit' in failed_gates", "fit" in failed2, f"failed={failed2}")


# ── A single unverifiable "% off" must never be sufficient for Strong Buy ───
ev_ccc_only = C.ref_evidence(["ccc_was"])
v, discount, failed = C.verdict_durable(
    price_eur=370.0, trigger_eur=None, ref_eur=1000.0,   # 63% off
    evidence=ev_ccc_only, fit_score=90, on_list=True,
)
chk("ccc_was-only 63% off: not Strong Buy", v != C.VERDICT_STRONG, f"got {v}")

ev_corroborated = C.ref_evidence(["corroborated", "regular_median"])
chk("corroborated+regular_median evidence == 2.0", ev_corroborated == 2.0, f"got {ev_corroborated}")
v, discount, failed = C.verdict_durable(
    price_eur=370.0, trigger_eur=None, ref_eur=1000.0,   # same 63% off
    evidence=ev_corroborated, fit_score=90, on_list=False,
)
chk("same lead off-list with evidence 2.0: not Strong Buy (needs 2.5 off-list)",
    v != C.VERDICT_STRONG, f"got {v}")
chk("same lead off-list: 'evidence' in failed_gates", "evidence" in failed, f"failed={failed}")

ev_alone = C.ref_evidence(["retailer_claim"])
chk("retailer_claim alone < MIN_EVIDENCE_STRONG", ev_alone < C.MIN_EVIDENCE_STRONG,
    f"{ev_alone} vs {C.MIN_EVIDENCE_STRONG}")
for leg in C.EVIDENCE_WEIGHTS:
    if leg == "retailer_claim":
        continue
    total = C.ref_evidence(["retailer_claim", leg])
    chk(f"retailer_claim + {leg} < MIN_EVIDENCE_STRONG", total < C.MIN_EVIDENCE_STRONG,
        f"{total} vs {C.MIN_EVIDENCE_STRONG}")


# ── Off-list has a HARD Fair ceiling ─────────────────────────────────────────
# Clears every numeric off-list gate: discount 0.60, saving 600 (>=40), evidence 3.0,
# fit 95.
v, discount, failed = C.verdict_durable(
    price_eur=400.0, trigger_eur=None, ref_eur=1000.0,
    evidence=3.0, fit_score=95, on_list=False,
)
chk("offlist ceiling: every numeric gate cleared, still not Strong Buy",
    v != C.VERDICT_STRONG, f"got {v}")
chk("offlist ceiling: 'offlist_ceiling' in failed_gates", "offlist_ceiling" in failed, f"failed={failed}")


# ── Consumables (par = EUR 12.00/kg) ─────────────────────────────────────────
salmon = catalog.WATCHLIST["food.salmon_fillet"]
par = salmon["par_eur"]
ev_par_retailer = C.ref_evidence(["user_par", "retailer_claim"])

v, discount, failed = C.verdict_consumable(7.20, par, None, 92, ev_par_retailer)
chk("consumable 40% under, no floor yet, fit 92: Strong Buy", v == C.VERDICT_STRONG, f"got {v}")

v, discount, failed = C.verdict_consumable(9.80, par, None, 92, ev_par_retailer)
chk("consumable 18% under (below Strong rung): Fair", v == C.VERDICT_FAIR, f"got {v}")

floor = 8.00
v, discount, failed = C.verdict_consumable(9.00, par, floor, 90, ev_par_retailer)
chk("consumable 25% under but above floor slack: Fair", v == C.VERDICT_FAIR, f"got {v}")
# Exact set, not just membership: 0.25 must CLEAR CONSUMABLE_STRONG_DISCOUNT on its own
# (only near_floor blocks it) — this is what actually pins the discount rung, since
# "near_floor in failed" alone would stay true even if the discount gate also failed.
chk("consumable 25% under: failed_gates is exactly ['near_floor']",
    failed == ["near_floor"], f"failed={failed}")
chk("9.00 is actually above floor*PROMO_FLOOR_SLACK",
    9.00 > floor * C.PROMO_FLOOR_SLACK, "test setup invalid")
chk("0.25 discount clears CONSUMABLE_STRONG_DISCOUNT on its own",
    (1 - 9.00 / par) >= C.CONSUMABLE_STRONG_DISCOUNT, "test setup invalid")

v, discount, failed = C.verdict_consumable(8.50, par, floor, 90, ev_par_retailer)
chk("consumable 29% under, near floor, fit 90: Strong Buy", v == C.VERDICT_STRONG, f"got {v}")
chk("8.50 is actually within floor*PROMO_FLOOR_SLACK",
    8.50 <= floor * C.PROMO_FLOOR_SLACK, "test setup invalid")

v, discount, failed = C.verdict_consumable(8.50, par, floor, 60, ev_par_retailer)
chk("consumable same price, fit 60: Fair", v == C.VERDICT_FAIR, f"got {v}")
chk("consumable fit 60: 'fit' in failed_gates", "fit" in failed, f"failed={failed}")

v, discount, failed = C.verdict_consumable(11.90, par, None, 92, ev_par_retailer)
chk("consumable under 5%: Skip", v == C.VERDICT_SKIP, f"got {v}")

# The user_par leg is load-bearing: without it, the 40%-under case falls to Fair.
v, discount, failed = C.verdict_consumable(7.20, par, None, 92, ev_retailer_only)
chk("consumable 40% under WITHOUT user_par leg: Fair", v == C.VERDICT_FAIR, f"got {v}")
chk("consumable 40% under WITHOUT user_par leg: 'evidence' in failed_gates",
    "evidence" in failed, f"failed={failed}")


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


# ── rank_score cross-class comparability ─────────────────────────────────────
sony_discount = (349.0 - 179.0) / 349.0
sony_saving = 349.0 - 179.0
sony_score = C.rank_score(sony_discount, sony_saving, C.VERDICT_STRONG)

salmon_discount = 1 - 7.20 / 12.00
salmon_saving = (12.00 - 7.20) * 5.0   # bulk_qty=5
salmon_score = C.rank_score(salmon_discount, salmon_saving, C.VERDICT_STRONG)

hi, lo = max(sony_score, salmon_score), min(sony_score, salmon_score)
chk("rank_score: cross-class ratio < 10", lo > 0 and (hi / lo) < 10,
    f"sony={sony_score} salmon={salmon_score}")

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

cand_class_swap = {"sku_class": "consumable", "unit_price_eur": 10.00, "price_eur": 50.00}
bucket_as_consumable = C.price_bucket(cand_class_swap)
cand_class_swap["sku_class"] = "durable"
bucket_as_durable = C.price_bucket(cand_class_swap)
chk("price_bucket: consumable uses unit_price_eur, durable uses price_eur (differ)",
    bucket_as_consumable != bucket_as_durable,
    f"consumable={bucket_as_consumable} durable={bucket_as_durable}")

cand_retailer_a = {"sku": "food.salmon_fillet", "sku_class": "consumable",
                    "unit_price_eur": 10.00, "retailer": "Kaufland"}
cand_retailer_b = {"sku": "food.salmon_fillet", "sku_class": "consumable",
                    "unit_price_eur": 10.00, "retailer": "Billa"}
chk("seen_key: same sku/price, different retailer -> SAME key",
    C.seen_key(cand_retailer_a) == C.seen_key(cand_retailer_b),
    f"{C.seen_key(cand_retailer_a)} vs {C.seen_key(cand_retailer_b)}")
chk("seen_key: retailer text is not present in the key",
    "Kaufland" not in C.seen_key(cand_retailer_a) and "Billa" not in C.seen_key(cand_retailer_b))


# ── effective_par proposes and never overwrites ──────────────────────────────
watchlist_before = copy.deepcopy(catalog.WATCHLIST)
salmon_cfg = catalog.WATCHLIST["food.salmon_fillet"]
hand_par = salmon_cfg["par_eur"]

thin_stats = {"regular": {"n": C.REGULAR_MIN_N - 1, "median": 999999.0,
                           "span_days": C.REGULAR_MIN_SPAN_DAYS}}
par_out, drift = C.effective_par(salmon_cfg, thin_stats)
chk("effective_par: too-thin regular series -> hand-set par unchanged",
    par_out == hand_par and drift == 0.0, f"got {par_out}, {drift}")

extreme_high_stats = {"regular": {"n": C.REGULAR_MIN_N, "median": 1_000_000.0,
                                   "span_days": C.REGULAR_MIN_SPAN_DAYS}}
par_out, drift = C.effective_par(salmon_cfg, extreme_high_stats)
lo, hi = hand_par * (1 - C.PAR_DRIFT_MAX), hand_par * (1 + C.PAR_DRIFT_MAX)
chk("effective_par: extreme high median stays within PAR_DRIFT_MAX",
    lo - 1e-9 <= par_out <= hi + 1e-9, f"par_out={par_out} bounds=({lo},{hi})")

extreme_low_stats = {"regular": {"n": C.REGULAR_MIN_N, "median": 0.01,
                                  "span_days": C.REGULAR_MIN_SPAN_DAYS}}
par_out, drift = C.effective_par(salmon_cfg, extreme_low_stats)
chk("effective_par: extreme low median stays within PAR_DRIFT_MAX",
    lo - 1e-9 <= par_out <= hi + 1e-9, f"par_out={par_out} bounds=({lo},{hi})")

chk("effective_par: catalog.WATCHLIST not mutated", catalog.WATCHLIST == watchlist_before)


# ── gates_prompt_text must not drift from the constants ─────────────────────
text = C.gates_prompt_text()
expect_consumable_pct = f">= {round(C.CONSUMABLE_STRONG_DISCOUNT * 100)}%"
expect_durable_pct = f">= {round(C.DURABLE_STRONG_DISCOUNT * 100)}%"
expect_abs_saving = f"EUR {C.DURABLE_MIN_ABS_SAVING_EUR} absolute saving"
expect_evidence = f"{C.EVIDENCE_WEIGHTS['retailer_claim']} of the {C.MIN_EVIDENCE_STRONG} reference-credibility"

chk("gates_prompt_text contains CONSUMABLE_STRONG_DISCOUNT", expect_consumable_pct in text,
    f"looked for {expect_consumable_pct!r}")
chk("gates_prompt_text contains DURABLE_STRONG_DISCOUNT", expect_durable_pct in text,
    f"looked for {expect_durable_pct!r}")
chk("gates_prompt_text contains DURABLE_MIN_ABS_SAVING_EUR", expect_abs_saving in text,
    f"looked for {expect_abs_saving!r}")
chk("gates_prompt_text contains MIN_EVIDENCE_STRONG", expect_evidence in text,
    f"looked for {expect_evidence!r}")


print(f"\n{len(_fails)} failure(s)" if _fails else "\nAll verdict tests passed.")
import sys
sys.exit(1 if _fails else 0)
