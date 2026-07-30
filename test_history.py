"""test_history.py — hand-rolled tests for history.py (no pytest, no network).

Runs entirely inside a temp sandbox (os.chdir + finally shutil.rmtree) so the repo's
real state/ directory is never touched. Verified at the bottom of this file.
"""

import copy
import datetime as dt
import os
import shutil
import tempfile

import config as C
import catalog
import history

_fails = []


def chk(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _fails.append(name)


def _promo_cand(sku, unit_price_eur, retailer="Kaufland", source="broshura",
                 price_eur=None, qty=None, name="test item", quarantine=False):
    return {
        "sku": sku, "sku_class": "consumable", "unit": "kg",
        "retailer": retailer, "source": source,
        "price_eur": price_eur if price_eur is not None else unit_price_eur,
        "qty": qty if qty is not None else 1.0,
        "unit_price_eur": unit_price_eur,
        "name": name, "quarantine": quarantine,
    }


def run():
    SKU = "food.salmon_fillet"

    # ── HEADLINE TEST: a promo observation must never move the regular median ──
    hist = history.load()
    for _ in range(10):
        history.record_promo(hist, _promo_cand(SKU, 8.0))
    for _ in range(4):
        history.record_regular(hist, SKU, 14.0, source="ccc")
    reg = history.stats_for(hist, SKU)["regular"]
    chk("headline: promo never moves regular median", reg["median"] == 14,
        f"got {reg['median']}")
    promo = history.stats_for(hist, SKU)["promo"]
    chk("headline: promo series unaffected by regular writes", promo["n"] == 10 and promo["median"] == 8.0)

    # ── record_regular refuses a broshura source ──
    hist2 = history.load()
    ok = history.record_regular(hist2, SKU, 10.0, source="broshura")
    entry2 = hist2.get("skus", {}).get(SKU, {})
    chk("record_regular refuses broshura source", ok is False)
    chk("record_regular: regular stays empty after refusal", entry2.get("regular", []) == [])

    # ── record_promo refuses a quarantined candidate ──
    hist3 = history.load()
    ok3 = history.record_promo(hist3, _promo_cand(SKU, 5.0, quarantine=True))
    chk("record_promo refuses quarantined candidate", ok3 is False)
    chk("record_promo: quarantined candidate leaves promo series empty",
        history.stats_for(hist3, SKU).get("promo", {}).get("n", 0) == 0)

    # ── 12 synthetic promo observations -> stats match C.percentile ──
    hist4 = history.load()
    vals = [10, 12, 9, 11, 15, 8, 13, 14, 10, 9, 12, 11]
    base = dt.date(2026, 1, 1)
    for i, v in enumerate(vals):
        history.record_promo(hist4, _promo_cand(SKU, float(v)))
        hist4["skus"][SKU]["promo"][-1]["d"] = (base + dt.timedelta(days=i)).isoformat()
    # dates were overwritten after the fact; refresh stats before asserting on "last"
    hist4["skus"][SKU]["stats"]["promo"] = history._promo_stats(hist4["skus"][SKU]["promo"])
    pstats = history.stats_for(hist4, SKU)["promo"]
    chk("12-obs promo n", pstats["n"] == 12, pstats)
    chk("12-obs promo min", pstats["min"] == min(vals), pstats)
    chk("12-obs promo p10 == C.percentile", pstats["p10"] == C.percentile(vals, C.PROMO_FLOOR_PERCENTILE), pstats)
    chk("12-obs promo median == C.percentile", pstats["median"] == C.percentile(vals, 0.5), pstats)
    chk("12-obs promo last == most recent by date", pstats["last"] == float(vals[-1]), pstats)

    # ── C.promo_floor: None below PROMO_FLOOR_MIN_N, real at/above it ──
    stats_thin = {"promo": {"n": C.PROMO_FLOOR_MIN_N - 1, "p10": 5.0}}
    stats_ok   = {"promo": {"n": C.PROMO_FLOOR_MIN_N, "p10": 5.0}}
    chk(f"promo_floor None at n={C.PROMO_FLOOR_MIN_N - 1}", C.promo_floor(stats_thin) is None)
    chk(f"promo_floor real at n={C.PROMO_FLOOR_MIN_N}", C.promo_floor(stats_ok) == 5.0)

    # ── C.effective_par: unchanged when thin, clamped within PAR_DRIFT_MAX when not ──
    sku_cfg = catalog.CATALOG[SKU]  # par_eur = 12.00
    catalog_before = copy.deepcopy(catalog.CATALOG)
    par = sku_cfg["par_eur"]

    thin_stats = {"regular": {"n": C.REGULAR_MIN_N - 1, "median": par * 3, "span_days": 100}}
    par_thin, drift_thin = C.effective_par(sku_cfg, thin_stats)
    chk("effective_par unchanged when regular series is thin", par_thin == par and drift_thin == 0.0,
        (par_thin, drift_thin))

    far_stats = {"regular": {"n": C.REGULAR_MIN_N + 1, "median": par * 3, "span_days": C.REGULAR_MIN_SPAN_DAYS + 5}}
    par_far, drift_far = C.effective_par(sku_cfg, far_stats)
    lo, hi = par * (1 - C.PAR_DRIFT_MAX), par * (1 + C.PAR_DRIFT_MAX)
    chk("effective_par never exceeds par*(1+PAR_DRIFT_MAX)", par_far <= hi + 1e-9, par_far)
    chk("effective_par never drops below par*(1-PAR_DRIFT_MAX)", par_far >= lo - 1e-9, par_far)
    chk("effective_par does not mutate catalog.CATALOG", catalog.CATALOG == catalog_before)

    # ── regular_median evidence conditions: n=3 unusable, n=4 spanning 21+ days usable ──
    hist5 = history.load()
    base5 = dt.date(2026, 1, 1)
    for i in range(3):
        history.record_regular(hist5, SKU, 14.0, source="ccc")
        hist5["skus"][SKU]["regular"][-1]["d"] = (base5 + dt.timedelta(days=i * 10)).isoformat()
    hist5["skus"][SKU]["stats"]["regular"] = history._regular_stats(hist5["skus"][SKU]["regular"])
    r3 = history.stats_for(hist5, SKU)["regular"]
    usable3 = r3["n"] >= C.REGULAR_MIN_N and r3["span_days"] >= C.REGULAR_MIN_SPAN_DAYS
    chk("regular_median: n=3 gives no usable median", usable3 is False, r3)

    history.record_regular(hist5, SKU, 14.0, source="ccc")
    hist5["skus"][SKU]["regular"][-1]["d"] = (base5 + dt.timedelta(days=30)).isoformat()
    hist5["skus"][SKU]["stats"]["regular"] = history._regular_stats(hist5["skus"][SKU]["regular"])
    r4 = history.stats_for(hist5, SKU)["regular"]
    usable4 = r4["n"] >= C.REGULAR_MIN_N and r4["span_days"] >= C.REGULAR_MIN_SPAN_DAYS
    chk("regular_median: n=4 spanning 21+ days is usable", usable4 is True, r4)

    # ── disc.* skus prune at DISC_SKU_MAX_DAYS, catalog skus survive to HISTORY_MAX_DAYS ──
    hist6 = history.load()
    disc_sku = "disc.mystery_snack"
    old_days = C.DISC_SKU_MAX_DAYS + 5   # older than disc TTL, younger than catalog TTL
    old_date = (dt.date.today() - dt.timedelta(days=old_days)).isoformat()

    history.record_promo(hist6, _promo_cand(disc_sku, 7.0))
    hist6["skus"][disc_sku]["promo"][-1]["d"] = old_date
    history.record_promo(hist6, _promo_cand(SKU, 7.0))
    hist6["skus"][SKU]["promo"][-1]["d"] = old_date

    history.prune(hist6)
    chk("disc.* sku's old observation is pruned at DISC_SKU_MAX_DAYS",
        len(hist6["skus"][disc_sku]["promo"]) == 0)
    chk("catalog sku's observation survives to HISTORY_MAX_DAYS",
        len(hist6["skus"][SKU]["promo"]) == 1)

    # ── prune keeps the newest MAX_OBS_PER_SKU, not the oldest ──
    hist7 = history.load()
    n_obs = C.MAX_OBS_PER_SKU + 10
    base7 = dt.date.today() - dt.timedelta(days=n_obs)
    for i in range(n_obs):
        history.record_promo(hist7, _promo_cand(SKU, float(i)))
        hist7["skus"][SKU]["promo"][-1]["d"] = (base7 + dt.timedelta(days=i)).isoformat()
    history.prune(hist7)
    kept = hist7["skus"][SKU]["promo"]
    chk("prune caps series at MAX_OBS_PER_SKU", len(kept) == C.MAX_OBS_PER_SKU, len(kept))
    kept_vals = sorted(o["unit_price_eur"] for o in kept)
    expected_vals = sorted(float(i) for i in range(n_obs - C.MAX_OBS_PER_SKU, n_obs))
    chk("prune keeps the NEWEST observations, not the oldest", kept_vals == expected_vals)

    # ── bump_health / stale_skus ──
    h = history.load_health()
    all_skus = ["food.salmon_fillet", "food.chicken_breast"]
    for _ in range(C.CATALOG_STALE_RUNS):
        history.bump_health(h, matched_skus=["food.salmon_fillet"], all_skus=all_skus)
    stale = history.stale_skus(h)
    chk("stale_skus surfaces a sku unmatched for CATALOG_STALE_RUNS runs",
        "food.chicken_breast" in stale, stale)
    chk("stale_skus does not surface a sku matched every run",
        "food.salmon_fillet" not in stale, stale)

    # ── save writes both price_history.json and history.md ──
    history.save(hist)
    chk("save writes state/price_history.json", os.path.exists(os.path.join("state", "price_history.json")))
    chk("save writes state/history.md", os.path.exists(os.path.join("state", "history.md")))


def main():
    real_history_path = os.path.join(os.getcwd(), "state", "price_history.json")
    with open(real_history_path, "rb") as f:
        before = f.read()

    sandbox = tempfile.mkdtemp(prefix="history_test_")
    cwd = os.getcwd()
    try:
        os.chdir(sandbox)
        run()
    finally:
        os.chdir(cwd)
        shutil.rmtree(sandbox, ignore_errors=True)

    with open(real_history_path, "rb") as f:
        after = f.read()
    chk("real state/price_history.json untouched by the test run", before == after)

    print(f"\n{len(_fails)} failure(s)" if _fails else "\nAll history tests passed.")
    import sys
    sys.exit(1 if _fails else 0)


if __name__ == "__main__":
    main()
