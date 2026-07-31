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
        history.record_regular(hist, SKU, 14.0, source="corroborate")
    reg = history.stats_for(hist, SKU)["regular"]
    chk("headline: promo never moves regular median", reg["median"] == 14,
        f"got {reg['median']}")
    promo = history.stats_for(hist, SKU)["promo"]
    chk("headline: promo series unaffected by regular writes", promo["n"] == 10 and promo["median"] == 8.0)

    # ── record_regular is an ALLOWLIST, not a denylist ──
    # Every harvest source is a promotions feed, so each must be refused. A denylist
    # would grant any newly-added source `regular` access by default and erode the par
    # silently — assert refusal for EVERY source the pipeline can actually produce, and
    # acceptance only for the Stage-5 corroborator.
    for bad_source in ("broshura", "ccc", "mydealz", "llm_discover", "", "unknown_feed"):
        hist2 = history.load()
        ok = history.record_regular(hist2, SKU, 10.0, source=bad_source)
        entry2 = hist2.get("skus", {}).get(SKU, {})
        chk(f"record_regular refuses source={bad_source!r}",
            ok is False and entry2.get("regular", []) == [])

    for good_source in sorted(C.REGULAR_ALLOWED_SOURCES):
        hist2b = history.load()
        okg = history.record_regular(hist2b, SKU, 10.0, source=good_source)
        chk(f"record_regular ACCEPTS source={good_source!r}",
            okg is not False and len(hist2b["skus"][SKU]["regular"]) == 1)

    # ── record_regular upserts on (d, product_code) ──
    # Two runs on one day used to double-record every Lidl shelf price: measured
    # 2026-07-31, state/price_history.json held 50 `regular` observations that were an
    # exact doubling of 25. That skews every percentile and inflates `n` against
    # REGULAR_MIN_N, so the store looks twice as well-evidenced as it is.
    histu = history.load()
    for _ in range(2):
        history.record_regular(histu, SKU, 12.5, source="lidl_regular",
                               retailer="Lidl", product_code="0001229", name="Сьомга филе")
    obs_u = histu["skus"][SKU]["regular"]
    chk("two record_regular calls for one product on one day yield ONE observation",
        len(obs_u) == 1, f"got {len(obs_u)}")
    chk("the surviving observation carries its identity",
        obs_u[0].get("product_code") == "0001229" and obs_u[0].get("retailer") == "Lidl"
        and obs_u[0].get("name") == "Сьомга филе", obs_u[0])
    chk("stats reflect the deduped series", history.stats_for(histu, SKU)["regular"]["n"] == 1)

    # A re-run with a CHANGED price must overwrite rather than be dropped — the newest
    # read of the same product on the same day is the one to keep.
    history.record_regular(histu, SKU, 11.0, source="lidl_regular",
                           retailer="Lidl", product_code="0001229", name="Сьомга филе")
    chk("an upsert replaces the value, it does not append or ignore",
        len(histu["skus"][SKU]["regular"]) == 1
        and histu["skus"][SKU]["regular"][0]["unit_price_eur"] == 11.0)

    # ...but DISTINCT products on the same day are distinct observations. `d` alone is
    # the wrong key: food.rice genuinely held three different rices on 2026-07-31, and
    # collapsing them would replace a real price spread with a single arbitrary row.
    histd = history.load()
    for code, price in (("A1", 3.49), ("B2", 1.53), ("C3", 2.99)):
        history.record_regular(histd, "food.rice", price, source="lidl_regular",
                               retailer="Lidl", product_code=code, name=f"rice {code}")
    chk("three distinct product codes on one day stay three observations",
        len(histd["skus"]["food.rice"]["regular"]) == 3)

    # A row with no product_code has no identity to dedupe on (the Stage-5 corroborate
    # path has none) and must still be recorded, never silently collapsed.
    histn = history.load()
    for _ in range(3):
        history.record_regular(histn, SKU, 14.0, source="corroborate")
    chk("observations with no product_code are always appended",
        len(histn["skus"][SKU]["regular"]) == 3)

    # ── the two series are capped SEPARATELY ──
    # `regular` takes one row per distinct Lidl product code per run — dozens a week on
    # the heavy skus — so the promo cap of 40 would wipe a sku's whole shelf history
    # every run and leave any rolling window blind while still looking populated.
    chk("REGULAR_MAX_OBS is much larger than the promo cap",
        C.REGULAR_MAX_OBS > C.MAX_OBS_PER_SKU * 10,
        f"promo={C.MAX_OBS_PER_SKU} regular={C.REGULAR_MAX_OBS}")

    histc = history.load()
    n_reg = C.MAX_OBS_PER_SKU + 25   # over the PROMO cap, well under the regular one
    for i in range(n_reg):
        history.record_regular(histc, SKU, float(i), source="lidl_regular",
                               retailer="Lidl", product_code=f"code{i}", name=f"p{i}")
    history.prune(histc)
    chk("prune does NOT apply the promo cap to the regular series",
        len(histc["skus"][SKU]["regular"]) == n_reg,
        f"kept {len(histc['skus'][SKU]['regular'])} of {n_reg}")

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

    # ── history.baseline_stats: the observed reference that replaced par_eur ──
    catalog_before = copy.deepcopy(catalog.CATALOG)

    histb = history.load()
    # Twelve shelf observations: 1..12, so p25 is deterministic under nearest-rank
    # (index ceil(0.25*12)-1 = 2 -> the third-smallest = 3.0).
    for i, price in enumerate(range(1, 13)):
        history.record_regular(histb, SKU, float(price), source="lidl_regular",
                               retailer="Lidl", product_code=f"code{i}", name=f"p{i}")
    b = history.baseline_stats(histb, SKU)
    chk("baseline_stats counts every in-window observation", b["n"] == 12, b)
    chk("baseline_stats p25 uses C.percentile, not a second implementation",
        b["p25"] == C.percentile([float(x) for x in range(1, 13)], C.BASELINE_PERCENTILE), b)
    chk("baseline_stats spread is p90/p10", b["spread"] == round(b["p90"] / b["p10"], 3), b)
    chk("a 1..12 series is WIDE by definition", b["spread"] > C.BASELINE_MAX_SPREAD, b)

    # A narrow series must read as high-confidence, or every sku is Fair-capped forever.
    histn = history.load()
    for i, price in enumerate([4.0, 4.2, 4.4, 4.6, 4.8, 5.0]):
        history.record_regular(histn, SKU, price, source="lidl_regular",
                               retailer="Lidl", product_code=f"n{i}", name=f"n{i}")
    bn = history.baseline_stats(histn, SKU)
    chk("a tight series is NOT flagged wide", bn["spread"] <= C.BASELINE_MAX_SPREAD, bn)

    # THE WINDOW IS LOAD-BEARING. Observations older than BASELINE_WINDOW_DAYS must be
    # excluded even though prune keeps them for HISTORY_MAX_DAYS (540) — the reference is
    # meant to track inflation, not average over a year and a half of it.
    histw = history.load()
    old_day = (dt.date.today() - dt.timedelta(days=C.BASELINE_WINDOW_DAYS + 30)).isoformat()
    for i in range(6):
        history.record_regular(histw, SKU, 99.0, source="lidl_regular",
                               retailer="Lidl", product_code=f"old{i}", name="stale")
        histw["skus"][SKU]["regular"][-1]["d"] = old_day
    for i in range(6):
        history.record_regular(histw, SKU, 5.0, source="lidl_regular",
                               retailer="Lidl", product_code=f"new{i}", name="fresh")
    bw = history.baseline_stats(histw, SKU)
    chk("baseline_stats excludes observations older than BASELINE_WINDOW_DAYS",
        bw["n"] == 6 and bw["p25"] == 5.0, bw)
    chk("...while the raw series still holds all 12 for inspection",
        len(histw["skus"][SKU]["regular"]) == 12)

    empty_b = history.baseline_stats(history.load(), "food.nonexistent_sku")
    chk("baseline_stats on an unknown sku is empty, not an exception",
        empty_b == {"n": 0, "p25": None, "p10": None, "p90": None, "spread": None}, empty_b)

    chk("baseline_stats does not mutate catalog.CATALOG", catalog.CATALOG == catalog_before)

    # ── regular_median evidence conditions: n=3 unusable, n=4 spanning 21+ days usable ──
    hist5 = history.load()
    base5 = dt.date(2026, 1, 1)
    for i in range(3):
        history.record_regular(hist5, SKU, 14.0, source="corroborate")
        hist5["skus"][SKU]["regular"][-1]["d"] = (base5 + dt.timedelta(days=i * 10)).isoformat()
    hist5["skus"][SKU]["stats"]["regular"] = history._regular_stats(hist5["skus"][SKU]["regular"])
    r3 = history.stats_for(hist5, SKU)["regular"]
    usable3 = r3["n"] >= C.REGULAR_MIN_N and r3["span_days"] >= C.REGULAR_MIN_SPAN_DAYS
    chk("regular_median: n=3 gives no usable median", usable3 is False, r3)

    history.record_regular(hist5, SKU, 14.0, source="corroborate")
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

    # ── ...and the regular series is capped at its OWN, larger, limit ──
    # Built by hand rather than through record_regular: the point is prune's cap, and
    # 1200+ upserts would only be testing the upsert scan.
    hist7b = history.load()
    entry7b = history._sku_entry(hist7b, SKU)
    # Dates are compressed into 121 days, ten observations apiece — which is the real
    # arrival shape (dozens of distinct product codes per weekly run) and keeps every
    # row inside HISTORY_MAX_DAYS so the CAP is what gets tested, not the TTL.
    n_reg7 = C.REGULAR_MAX_OBS + 10
    base7b = dt.date.today() - dt.timedelta(days=n_reg7 // 10)
    entry7b["regular"] = [
        {"d": (base7b + dt.timedelta(days=i // 10)).isoformat(), "source": "lidl_regular",
         "unit_price_eur": float(i), "note": "", "retailer": "Lidl",
         "product_code": f"c{i}", "name": f"p{i}"}
        for i in range(n_reg7)
    ]
    history.prune(hist7b)
    kept7b = hist7b["skus"][SKU]["regular"]
    chk("prune caps the regular series at REGULAR_MAX_OBS",
        len(kept7b) == C.REGULAR_MAX_OBS, len(kept7b))
    chk("regular prune keeps the NEWEST observations",
        min(o["unit_price_eur"] for o in kept7b) == float(n_reg7 - C.REGULAR_MAX_OBS))

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
