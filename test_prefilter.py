"""test_prefilter.py — hand-rolled tests for prefilter.py, no pytest, no network."""

import config as C
import catalog
import match
from prefilter import prefilter

TODAY = "2026-07-30"

REASONS = {
    "expired", "no_price", "no_sku_match", "over_reference", "dup", "over_cap",
}


def offer(**kw):
    """A full offer/candidate dict, contract defaults, overridden by kw."""
    o = {
        "source": "llm_discover", "retailer": "Kaufland", "name": "Item",
        "price_eur": 10.0, "was_price_eur": None, "claimed_discount": None,
        "valid_until": None, "url": "", "heat": None,
        "raw": "", "sku": None, "sku_class": None, "match_conf": None,
        "qty": None, "unit": None, "unit_price_eur": None, "pending_qty": False,
    }
    o.update(kw)
    return o


# ── 4000 synthetic offers across 10 deterministic patterns ───────────────────
SOURCES = ["ccc", "mydealz", "llm_discover"]


def build_bulk(n=4000):
    out = []
    for i in range(n):
        src = SOURCES[i % len(SOURCES)]
        retailer = f"Retailer{i}"
        pattern = i % 10
        if pattern == 0:
            # good consumable: below par*slack
            out.append(offer(
                source=src, retailer=retailer, sku="food.chicken_breast",
                sku_class="consumable", match_conf="high",
                price_eur=5.0, qty=1.0, unit="kg", unit_price_eur=5.0,
            ))
        elif pattern == 1:
            # over_reference consumable: well above reference*slack (6.00 * 1.15 = 6.9)
            out.append(offer(
                source=src, retailer=retailer, sku="food.chicken_breast",
                sku_class="consumable", match_conf="high",
                price_eur=9.0, qty=1.0, unit="kg", unit_price_eur=9.0,
            ))
        elif pattern == 2:
            # good consumable: a second good one, below reference*slack
            out.append(offer(
                source=src, retailer=retailer, sku="food.olive_oil",
                sku_class="consumable", match_conf="high",
                price_eur=7.0, qty=1.0, unit="L", unit_price_eur=7.0,
            ))
        elif pattern == 3:
            # over_reference consumable: well above reference*slack (9.00 * 1.15 = 10.35)
            out.append(offer(
                source=src, retailer=retailer, sku="food.olive_oil",
                sku_class="consumable", match_conf="high",
                price_eur=12.0, qty=1.0, unit="L", unit_price_eur=12.0,
            ))
        elif pattern == 4:
            # consumable with no usable unit price: prefilter has no opinion, survives
            out.append(offer(
                source=src, retailer=retailer, sku="food.chicken_breast",
                sku_class="consumable", match_conf="high",
                price_eur=5.0, qty=None, unit=None, unit_price_eur=None,
            ))
        elif pattern == 5:
            # expired
            out.append(offer(
                source=src, retailer=retailer, sku="food.olive_oil",
                sku_class="consumable", match_conf="high",
                price_eur=8.0, qty=1.0, unit="L", unit_price_eur=8.0,
                valid_until="2026-01-01",
            ))
        elif pattern == 6:
            # no_price
            out.append(offer(source=src, retailer=retailer, price_eur=None))
        elif pattern == 7:
            # unmatched offer: no sku, no match -> no_sku_match
            out.append(offer(
                source=src, retailer=retailer, name=f"Some Gadget {i}",
                price_eur=100.0, was_price_eur=250.0, claimed_discount=0.60,
            ))
        elif pattern == 8:
            # unmatched offer: no sku, no match -> no_sku_match
            out.append(offer(
                source=src, retailer=retailer, name=f"Cheap Food Deal {i}",
                price_eur=200.0, was_price_eur=1000.0, claimed_discount=0.95,
            ))
        else:
            # unmatched offer: no sku, no match -> no_sku_match
            out.append(offer(
                source=src, retailer=retailer, name=f"Weak Gadget {i}",
                price_eur=61.0, was_price_eur=70.0, claimed_discount=0.10,
            ))
    return out


_fails = []


def chk(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _fails.append(name)


# ── Run the bulk batch ────────────────────────────────────────────────────────
bulk = build_bulk(4000)
# The observed shelf baselines the reference is computed from. These replace the
# hardcoded par_eur values these tests used to read out of the catalog: the numbers are
# deliberately the SAME (chicken 6.00/kg, olive oil 9.00/L) so every threshold below
# stays pinned to exactly the value it was calibrated at.
#
# `spread` is narrow on purpose. prefilter does not care about confidence — it only
# needs a number — but a wide spread here would make these fixtures quietly unlike the
# ones test_verdicts uses, and the two files must not disagree about what a baseline is.
def _baseline(p25):
    return {"n": 20, "p25": p25, "p10": p25 * 0.9, "p90": p25 * 1.2, "spread": 1.333}

BASELINES = {
    "food.chicken_breast": _baseline(6.00),
    "food.olive_oil": _baseline(9.00),
}

candidates, rejects, stats = prefilter(bulk, TODAY, BASELINES)

sources_present = {o.get("source") for o in bulk}
cap_sum = sum(C.SOURCE_CAPS.get(s, 0) for s in sources_present)
chk("len(candidates) <= sum of caps for sources present",
    len(candidates) <= cap_sum,
    f"got {len(candidates)}, cap sum {cap_sum}")

chk("every reject carries a non-empty reject_reason in the six names",
    all(r.get("reject_reason") in REASONS for r in rejects),
    f"bad reasons: {sorted({r.get('reject_reason') for r in rejects} - REASONS)}")

chk("no over_reference offer ever reaches candidates",
    all(not (c.get("sku_class") == "consumable"
             and c.get("unit_price_eur") is not None
             and (BASELINES.get(c.get("sku")) or {}).get("p25")
             and c.get("unit_price_eur") >
             BASELINES[c["sku"]]["p25"] * C.PREFILTER_REFERENCE_SLACK)
        for c in candidates),
    "an over-reference consumable leaked into candidates")
chk("the over_reference rule actually fired on this fixture",
    stats["rejects_by_reason"].get("over_reference", 0) > 0,
    stats["rejects_by_reason"])


# ── NO REFERENCE MUST NEVER MEAN A REJECTION ────────────────────────────
# A sku with no shelf history yet has nothing to compare against, and Stage 4 has not
# run at this point so there is no LLM reference either. Rejecting on that would
# silently and permanently discard every not-yet-observed sku — SOURCE_CAPS is the cost
# bound here, not this rule. This is the single most dangerous way the switch from
# par_eur to an observed reference could go wrong, because it fails as a quiet week.
expensive = offer(source="ccc", retailer="R", sku="food.chicken_breast",
                  sku_class="consumable", match_conf="high",
                  price_eur=99.0, qty=1.0, unit="kg", unit_price_eur=99.0)
c_none, r_none, _s = prefilter([dict(expensive)], TODAY)          # no baselines at all
chk("no baselines map -> a wildly over-priced consumable is NOT rejected",
    len(c_none) == 1 and r_none == [], (len(c_none), [r.get("reject_reason") for r in r_none]))

c_empty, r_empty, _s = prefilter([dict(expensive)], TODAY, {})     # empty map
chk("empty baselines map -> still no rejection", len(c_empty) == 1 and r_empty == [])

c_unseen, r_unseen, _s = prefilter([dict(expensive)], TODAY,
                                   {"food.olive_oil": _baseline(9.00)})  # other sku only
chk("a baselines map missing THIS sku -> still no rejection",
    len(c_unseen) == 1 and r_unseen == [])

# ...but WITH a reference, the same offer must be rejected. Otherwise the assertions
# above would pass for a version of prefilter that never rejects anything.
c_ref, r_ref, _s = prefilter([dict(expensive)], TODAY, BASELINES)
chk("the SAME offer with a reference present IS rejected over_reference",
    c_ref == [] and len(r_ref) == 1 and r_ref[0]["reject_reason"] == "over_reference",
    [r.get("reject_reason") for r in r_ref])

# A survivor carries the reference it was judged against, so the email's reject footer
# can print the real numbers instead of a bare reason string.
chk("the reject records the reference it was measured against",
    r_ref[0].get("reference_eur") == 6.00, r_ref[0].get("reference_eur"))

chk("discovery never admits a consumable, whatever its claimed discount",
    all(not (c.get("sku", "").startswith("disc.") and c.get("sku_class") == "consumable")
        for c in candidates),
    "an off-list consumable reached candidates")

chk("rejects_by_reason sums with n_out to n_in",
    sum(stats["rejects_by_reason"].values()) + stats["n_out"] == stats["n_in"],
    f"{sum(stats['rejects_by_reason'].values())} + {stats['n_out']} != {stats['n_in']}")

chk("n_in/n_out match the actual list lengths",
    stats["n_in"] == len(bulk) and stats["n_out"] == len(candidates))

# The pattern-8 consumable-hint discovery attempts must all be cut as no_sku_match.
consumable_hint_leads = [o for i, o in enumerate(bulk) if i % 10 == 8]
consumable_hint_rejects = [r for r in rejects if r.get("name", "").startswith("Cheap Food Deal")]
chk("consumable-hint discovery leads are all rejected no_sku_match",
    len(consumable_hint_rejects) == len(consumable_hint_leads)
    and all(r.get("reject_reason") == "no_sku_match" for r in consumable_hint_rejects),
    f"{len(consumable_hint_rejects)} of {len(consumable_hint_leads)} accounted for")


# ── Named regression scenarios (isolated, small, deterministic lists) ────────

# Dedup keeps the CHEAPEST unit price, not the first seen
d1 = offer(source="llm_discover", retailer="Lidl", sku="food.salmon_fillet",
           sku_class="consumable", match_conf="high",
           price_eur=11.0, qty=1.0, unit="kg", unit_price_eur=11.0)
d2 = offer(source="llm_discover", retailer="Lidl", sku="food.salmon_fillet",
           sku_class="consumable", match_conf="high",
           price_eur=9.5, qty=1.0, unit="kg", unit_price_eur=9.5)
cands, rejs, _ = prefilter([d1, d2], TODAY)
chk("dedup keeps the cheapest unit price, not the first seen",
    len(cands) == 1 and cands[0]["unit_price_eur"] == 9.5
    and len(rejs) == 1 and rejs[0]["reject_reason"] == "dup"
    and rejs[0]["unit_price_eur"] == 11.0,
    f"kept={cands[0].get('unit_price_eur') if cands else None}")

# over_cap drops the LEAST attractive: seed one source with cap+5 valid consumables
# at known discounts, assert survivors are the top `cap` by discount_vs_par.
cap = C.SOURCE_CAPS["ccc"]
par = BASELINES["food.olive_oil"]["p25"]  # the observed 9.00/L reference
n_seed = cap + 5
seed = []
discounts = []
for i in range(n_seed):
    # spread discounts 1%..n_seed% below par, each retailer distinct so dedup is a no-op
    disc = 0.01 + i * 0.01
    unit_price = par * (1 - disc)
    discounts.append(round(disc, 4))
    seed.append(offer(source="ccc", retailer=f"R{i}", sku="food.olive_oil",
                       sku_class="consumable", match_conf="high",
                       price_eur=unit_price, qty=1.0, unit="L",
                       unit_price_eur=unit_price))
cands, rejs, _ = prefilter(seed, TODAY, BASELINES)
kept_discounts = sorted((round(1 - c["unit_price_eur"] / par, 4) for c in cands), reverse=True)
expected_top = sorted(discounts, reverse=True)[:cap]
chk("over_cap keeps the top `cap` by discount vs the observed reference, drops the rest",
    len(cands) == cap and kept_discounts == expected_top,
    f"kept {len(cands)} of cap {cap}; kept_discounts={kept_discounts} expected={expected_top}")
chk("over_cap overflow is rejected over_cap",
    len(rejs) == n_seed - cap and all(r["reject_reason"] == "over_cap" for r in rejs))


print(f"\n{len(_fails)} failure(s)" if _fails else "\nAll prefilter tests passed.")
import sys
sys.exit(1 if _fails else 0)
