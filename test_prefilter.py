"""test_prefilter.py — hand-rolled tests for prefilter.py, no pytest, no network."""

import config as C
import catalog
import match
from prefilter import prefilter

TODAY = "2026-07-30"

REASONS = {
    "expired", "no_price", "no_sku_match", "over_par",
    "shallow_claim", "tiny_ticket", "dup", "over_cap",
}


def offer(**kw):
    """A full offer/candidate dict, contract defaults, overridden by kw."""
    o = {
        "source": "llm_discover", "retailer": "Kaufland", "name": "Item",
        "price_eur": 10.0, "was_price_eur": None, "claimed_discount": None,
        "valid_until": None, "url": "", "heat": None, "category_hint": None,
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
            # over_par consumable: well above par*slack (par 6.00 * 1.15 = 6.9)
            out.append(offer(
                source=src, retailer=retailer, sku="food.chicken_breast",
                sku_class="consumable", match_conf="high",
                price_eur=9.0, qty=1.0, unit="kg", unit_price_eur=9.0,
            ))
        elif pattern == 2:
            # good durable: trigger hit (av.sony_xm5 trigger 200.00)
            out.append(offer(
                source=src, retailer=retailer, sku="av.sony_xm5",
                sku_class="durable", match_conf="high",
                price_eur=190.0, was_price_eur=250.0, claimed_discount=0.24,
            ))
        elif pattern == 3:
            # shallow_claim durable: no trigger hit, weak claimed discount
            out.append(offer(
                source=src, retailer=retailer, sku="kitchen.airfryer",
                sku_class="durable", match_conf="high",
                price_eur=180.0, was_price_eur=200.0, claimed_discount=0.10,
            ))
        elif pattern == 4:
            # tiny_ticket durable: cheap ticket even with a steep claim
            out.append(offer(
                source=src, retailer=retailer, sku="tech.nas_hdd_4tb",
                sku_class="durable", match_conf="medium",
                price_eur=20.0, was_price_eur=50.0, claimed_discount=0.60,
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
            # discovery-eligible durable: no sku, durable hint, clears both thresholds
            out.append(offer(
                source=src, retailer=retailer, name=f"Some Gadget {i}",
                price_eur=100.0, was_price_eur=250.0, claimed_discount=0.60,
                category_hint="elektronik",
            ))
        elif pattern == 8:
            # discovery-ineligible: consumable hint, no sku -> must be cut regardless
            out.append(offer(
                source=src, retailer=retailer, name=f"Cheap Food Deal {i}",
                price_eur=200.0, was_price_eur=1000.0, claimed_discount=0.95,
                category_hint="хранителни стоки",
            ))
        else:
            # discovery-ineligible: durable hint but below the thresholds
            out.append(offer(
                source=src, retailer=retailer, name=f"Weak Gadget {i}",
                price_eur=61.0, was_price_eur=70.0, claimed_discount=0.10,
                category_hint="elektronik",
            ))
    return out


_fails = []


def chk(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _fails.append(name)


# ── Run the bulk batch ────────────────────────────────────────────────────────
bulk = build_bulk(4000)
candidates, rejects, stats = prefilter(bulk, TODAY)

sources_present = {o.get("source") for o in bulk}
cap_sum = sum(C.SOURCE_CAPS.get(s, 0) for s in sources_present)
chk("len(candidates) <= sum of caps for sources present",
    len(candidates) <= cap_sum,
    f"got {len(candidates)}, cap sum {cap_sum}")

chk("every reject carries a non-empty reject_reason in the eight names",
    all(r.get("reject_reason") in REASONS for r in rejects),
    f"bad reasons: {sorted({r.get('reject_reason') for r in rejects} - REASONS)}")

chk("no over_par offer ever reaches candidates",
    all(not (c.get("sku_class") == "consumable"
             and c.get("unit_price_eur") is not None
             and c.get("unit_price_eur") >
             (catalog.CATALOG.get(c.get("sku")) or {}).get("par_eur", 0) * C.PREFILTER_PAR_SLACK)
        for c in candidates),
    "an over-par consumable leaked into candidates")

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

# Washing-machine regression: durable, 10% claimed discount, no trigger hit -> shallow_claim
wm = offer(source="llm_discover", retailer="Technopolis", sku="test.washing_machine",
           sku_class="durable", match_conf="high",
           name="Washing machine", price_eur=450.0, was_price_eur=500.0,
           claimed_discount=0.10)
cands, rejs, _ = prefilter([wm], TODAY)
chk("washing machine at 10% off, no trigger hit, is rejected shallow_claim",
    len(cands) == 0 and len(rejs) == 1 and rejs[0]["reject_reason"] == "shallow_claim",
    f"candidates={len(cands)} rejects={[r.get('reject_reason') for r in rejs]}")

# 60% off a EUR 9 item -> tiny_ticket
cheap = offer(source="llm_discover", retailer="Metro", sku="test.cheap_gadget",
              sku_class="durable", match_conf="high",
              name="Cheap gadget", price_eur=3.6, was_price_eur=9.0,
              claimed_discount=0.60)
cands, rejs, _ = prefilter([cheap], TODAY)
chk("60% off a EUR 9 item is rejected tiny_ticket",
    len(cands) == 0 and len(rejs) == 1 and rejs[0]["reject_reason"] == "tiny_ticket",
    f"candidates={len(cands)} rejects={[r.get('reject_reason') for r in rejs]}")

# Wishlist durable AT its trigger price survives even with only a 5% claimed discount
trig = offer(source="llm_discover", retailer="Amazon.de", sku="av.sony_xm5",
             sku_class="durable", match_conf="high",
             name="Sony WH-1000XM5", price_eur=200.0, was_price_eur=210.0,
             claimed_discount=0.05)
cands, rejs, _ = prefilter([trig], TODAY)
chk("a wishlist durable AT its trigger price survives a weak 5% claim",
    len(cands) == 1 and len(rejs) == 0,
    f"candidates={len(cands)} rejects={[r.get('reject_reason') for r in rejs]}")

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
par = catalog.CATALOG["food.olive_oil"]["par_eur"]  # 9.00
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
cands, rejs, _ = prefilter(seed, TODAY)
kept_discounts = sorted((round(1 - c["unit_price_eur"] / par, 4) for c in cands), reverse=True)
expected_top = sorted(discounts, reverse=True)[:cap]
chk("over_cap keeps the top `cap` by discount_vs_par, drops the rest",
    len(cands) == cap and kept_discounts == expected_top,
    f"kept {len(cands)} of cap {cap}; kept_discounts={kept_discounts} expected={expected_top}")
chk("over_cap overflow is rejected over_cap",
    len(rejs) == n_seed - cap and all(r["reject_reason"] == "over_cap" for r in rejs))


print(f"\n{len(_fails)} failure(s)" if _fails else "\nAll prefilter tests passed.")
import sys
sys.exit(1 if _fails else 0)
