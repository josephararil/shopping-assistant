"""prefilter.py — Stage 2, the cost governor.

A single leaflet aggregator can yield well over a thousand offers a week. Feeding that
to an LLM is unaffordable and unfocused, so this module cuts the raw offer set down to
<= sum(C.SOURCE_CAPS) BEFORE any LLM sees anything. Every rejection is recorded with a
reason, because the weekly email's reject footer renders those reasons with real numbers.

The cap is deliberately stated as sum(C.SOURCE_CAPS) and never as a literal: the caps
were re-split once already (broshura out, llm_discover up) and every prose copy of the
old total silently went stale.

Pure, offline, stdlib only. See the scratchpad CONTRACT.md and config.py's module
docstring (lines 12-44) for the offer/candidate shape this binds to.

ONE public function: prefilter(offers, today, baselines=None) -> (candidates, rejects, stats)
"""

import config as C
import catalog
import match

# Eight reject reasons, first rule wins:
#   expired, no_price, no_sku_match, over_reference, shallow_claim, tiny_ticket, dup, over_cap


def _discovery_ok(offer):
    """Off-list discovery eligibility (durables only). Consumable discovery is cut
    entirely: with no par there is nothing to compute a unit price against."""
    claimed = offer.get("claimed_discount")
    price = offer.get("price_eur")
    hint = (offer.get("category_hint") or "").lower()
    return (
        (claimed or 0) >= C.DISCOVERY_MIN_CLAIMED_DISCOUNT
        and price is not None
        and price >= C.DISCOVERY_MIN_PRICE_EUR
        and catalog.CATEGORY_HINTS.get(hint) == "durable"
    )


def _reference_for(offer, baselines):
    """The observed reference to prefilter against, or None.

    None is a legitimate and common answer — a sku with no shelf history yet has nothing
    to compare against, and Stage 4 has not run at this point so there is no LLM
    reference either. Callers must treat that as "no opinion" and NEVER as a rejection:
    SOURCE_CAPS is the cost bound here, not this rule, and rejecting for want of a
    reference would silently discard every not-yet-observed sku forever."""
    if baselines is None:
        return None
    reference, _level, _conf = C.reference_for(offer, baselines.get(offer.get("sku")))
    return reference


def _reject_reason(offer, today, baselines):
    """Per-offer reject rules, first rule wins. Mutates `offer` in place with the
    sku/sku_class/match_conf/on_list fields the discovery path or a catalog match
    establishes, so downstream ordering can read them. Returns the reason string,
    or None if the offer survives this stage."""
    valid_until = offer.get("valid_until")
    if valid_until is not None and valid_until < today:
        return "expired"

    price = offer.get("price_eur")
    if price is None or price <= 0:
        return "no_price"

    sku = offer.get("sku")
    if not sku:
        if not _discovery_ok(offer):
            return "no_sku_match"
        sku = "disc." + match.slug(offer.get("name") or "")
        offer["sku"] = sku
        offer["sku_class"] = "durable"
        offer["match_conf"] = "medium"
        offer["on_list"] = False
    else:
        offer["on_list"] = True

    sku_class = offer.get("sku_class")
    if sku_class == "consumable":
        reference = _reference_for(offer, baselines)
        unit_price = offer.get("unit_price_eur")
        offer["reference_eur"] = reference
        if (reference and unit_price is not None
                and unit_price > reference * C.PREFILTER_REFERENCE_SLACK):
            return "over_reference"
    elif sku_class == "durable":
        trigger_eur = (catalog.CATALOG.get(sku) or {}).get("trigger_eur")
        trigger_hit = trigger_eur is not None and price <= trigger_eur
        if not trigger_hit:
            claimed = offer.get("claimed_discount")
            if (claimed or 0) < C.DURABLE_MIN_CLAIMED_DISCOUNT:
                return "shallow_claim"
        if price < C.DURABLE_MIN_PRICE_EUR:
            return "tiny_ticket"

    return None


def _attractiveness(offer, baselines):
    """Cap-fill ordering key, descending. mydealz heat earns a place here only —
    never in a field the verdict stage could read as evidence."""
    if offer.get("sku_class") == "consumable":
        reference = _reference_for(offer, baselines)
        unit_price = offer.get("unit_price_eur")
        if not reference or unit_price is None:
            return float("-inf")
        return 1 - unit_price / reference

    sku = offer.get("sku")
    trigger_eur = (catalog.CATALOG.get(sku) or {}).get("trigger_eur")
    price = offer.get("price_eur")
    if trigger_eur is not None and price is not None and price <= trigger_eur:
        return float("inf")  # a trigger hit sorts first, ahead of everything else
    heat = offer.get("heat") or 0
    claimed = offer.get("claimed_discount") or 0
    return claimed * (1 + min(C.HEAT_ORDER_MAX, heat / C.HEAT_ORDER_DIVISOR))


def _dedup_metric(offer):
    """The number 'dup' dedupes on: unit price for consumables, ticket price for
    durables (durables have no unit-price concept)."""
    if offer.get("sku_class") == "consumable":
        v = offer.get("unit_price_eur")
    else:
        v = offer.get("price_eur")
    return v if v is not None else float("inf")


def prefilter(offers, today, baselines=None):
    """Cut the raw offer set to <= sum(C.SOURCE_CAPS) before any LLM sees anything.

    `offers` are offer dicts (already annotated by match.annotate() where a catalog
    sku was found). `today` is an ISO "YYYY-MM-DD" string. `baselines` is an optional
    {sku: history.baseline_stats(...)} map; omit it and no consumable is rejected on
    price, which is the correct behaviour when no shelf history exists.

    Returns (candidates, rejects, stats):
      candidates          new list of offer dicts that survived, unmarked otherwise
      rejects             new list of offer dicts with `reject_reason` added
      stats               {"rejects_by_reason": {reason: count},
                            "kept_by_source": {source: count},
                            "n_in": int, "n_out": int}

    Does not mutate the input list or its dicts; every returned dict is a copy.
    """
    rejects = []
    rejects_by_reason = {}

    def _reject(o, reason):
        o["reject_reason"] = reason
        rejects_by_reason[reason] = rejects_by_reason.get(reason, 0) + 1
        rejects.append(o)

    survivors = []
    for offer in offers:
        o = dict(offer)
        reason = _reject_reason(o, today, baselines)
        if reason:
            _reject(o, reason)
        else:
            survivors.append(o)

    # dedup: same (sku, retailer) keeps the CHEAPEST metric; the rest are `dup`.
    groups = {}
    for o in survivors:
        groups.setdefault((o.get("sku"), o.get("retailer")), []).append(o)

    deduped = []
    for group in groups.values():
        if len(group) == 1:
            deduped.append(group[0])
            continue
        best = min(group, key=_dedup_metric)
        deduped.append(best)
        for o in group:
            if o is not best:
                _reject(o, "dup")

    # cap fill: attractiveness order per source, first C.SOURCE_CAPS[source] survive.
    by_source = {}
    for o in deduped:
        by_source.setdefault(o.get("source"), []).append(o)

    candidates = []
    for source, group in by_source.items():
        cap = C.SOURCE_CAPS.get(source)
        if cap is None:
            # Never fall back to 0: that would discard the whole source in silence, which
            # is indistinguishable from a quiet week. Warn loudly and let some through.
            cap = C.DEFAULT_SOURCE_CAP
            print(f"  [prefilter WARNING] source {source!r} has no SOURCE_CAPS entry — "
                  f"falling back to DEFAULT_SOURCE_CAP={cap}. Add it to config.SOURCE_CAPS.")
        ordered = sorted(group, key=lambda o: _attractiveness(o, baselines), reverse=True)
        for o in ordered[:cap]:
            candidates.append(o)
        for o in ordered[cap:]:
            _reject(o, "over_cap")

    kept_by_source = {}
    for o in candidates:
        src = o.get("source")
        kept_by_source[src] = kept_by_source.get(src, 0) + 1

    stats = {
        "rejects_by_reason": rejects_by_reason,
        "kept_by_source": kept_by_source,
        "n_in": len(offers),
        "n_out": len(candidates),
    }
    return candidates, rejects, stats
