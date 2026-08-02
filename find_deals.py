"""find_deals.py — the 8-stage Shop Hunter driver.

Stage 0 · HARVEST          deterministic   sources.py    ccc + mydealz RSS + Lidl export
Stage 1 · NORMALISE+MATCH  deterministic   match.py      done inside sources.harvest()
Stage 1b· RECORD LIDL REG. deterministic   history.py    Lidl statutory shelf prices -> regular
Stage 2 · PREFILTER        deterministic   prefilter.py  -> <=sum(SOURCE_CAPS) candidates
Stage 3 · DISCOVER         LLM #1 (search) the consumable source; Metro + silabg
Stage 4 · AUDIT            LLM #2 (batched, no search)   the procurement audit
Stage 5 · CORROBORATE      LLM #3 (search, gated, <=6)   only leads missing their evidence bar
Stage 6 · VERDICT          deterministic   config.py     Strong Buy / Fair / Skip
Stage 7 · DIGEST + STATE   deterministic   email, price_history, ledger, seen, deals_history

See CLAUDE.md for the invariants this file must hold — most importantly: the LLM never
performs arithmetic, promo/regular series never mix, and quarantine != skip.

Flags:
  --dry-run                   Stages 0-2 against the live web, exits before any LLM call.
  SHOP_HUNTER_DRY_RUN=1        every stage, writes state, sends no email, no mark_seen.
"""

import html
import json
import os
import sys
import datetime as dt
import urllib.parse

try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass

import config as C
import common as X
import catalog
import match
import sources
import prefilter
import history


# ── run-log helpers ──────────────────────────────────────────────────────────

def _section(title):
    print(f"\n{'=' * 66}\n  {title}\n{'=' * 66}")


def _esc(s):
    return html.escape(str(s) if s is not None else "", quote=True)


def _safe_url(u):
    return urllib.parse.quote(u or "", safe=":/?&=#%")


def _fmt_date(iso):
    if not iso:
        return ""
    try:
        d = dt.date.fromisoformat(iso)
        return f"{d.day} {d.strftime('%b')}"
    except Exception:
        return iso


def _match_by_lead_id(items, lead_id):
    return next((c for c in items if str(c.get("lead_id")) == str(lead_id)), None)


# ── Stage 1b: Lidl statutory regular prices ─────────────────────────────────

def _record_lidl_regulars(hist, regular_rows):
    """For every Lidl regular_row that matches a catalog sku, record its unit price
    into the `regular` series. This fails SILENTLY if forgotten — the evidence leg
    just stays absent — which is exactly why it is asserted in test_stub.py.

    `net_qty` is threaded through so annotate can use the statutory declaration rather
    than parsing the product title, and `product_code` so the observation carries an
    identity — without it two runs on one day double-record every shelf price and two
    genuinely different rices are indistinguishable from one rice seen twice."""
    n = 0
    for row in regular_rows or []:
        synth = {
            "name": row.get("name"),
            "price_eur": row.get("price_eur"),
            "net_qty": row.get("net_qty"),
        }
        match.annotate(synth)
        sku = synth.get("sku")
        unit_price = synth.get("unit_price_eur")
        if not sku or sku not in catalog.CATALOG or unit_price is None:
            continue
        if history.record_regular(
            hist, sku, unit_price, source="lidl_regular",
            retailer="Lidl", product_code=row.get("product_code"), name=row.get("name"),
        ):
            n += 1
    return n


# ── Stage 3: DISCOVER ────────────────────────────────────────────────────────

_ALWAYS_CHECK_TEXT = (
    "- Metro Bulgaria's weekly leaflet/online catalogue (metro.bg) — food and household "
    "staples, checked every week regardless of the gap list above.\n"
    "- silabg.com's promotions. NOTE: silabg.com/promocii returned HTTP 404 as of "
    "2026-07-30 — do not assume that path still works. Find the CURRENT live promotions "
    "URL yourself (site navigation or a search engine) rather than guessing the old one."
)


def _gap_skus_text(matched_skus):
    items = [(sku, cfg) for sku, cfg in catalog.CATALOG.items()
             if cfg.get("class") == "consumable" and sku not in matched_skus]
    items = items[:C.MAX_GAP_QUERIES]
    if not items:
        return "(none — every consumable matched a deterministic feed this run)"
    return "\n".join(
        f"{sku} — {cfg.get('label', '')} (unit: {cfg.get('unit')})"
        for sku, cfg in items
    )


def _offer_from_discover(o, sku, cfg):
    """Build a contract-shaped offer dict from a validated Stage-3 lead. Never trusts
    the model's arithmetic: unit_price_eur is recomputed via match.to_base from the
    transcribed pack_qty/pack_unit, exactly like match.annotate does for real offers."""
    offer = {
        "source": "llm_discover",
        "retailer": catalog.RETAILER_ALIASES.get((o.get("retailer") or "").strip().lower(),
                                                   (o.get("retailer") or "").strip().title() or None),
        "name": o.get("name"),
        "price_eur": o.get("price_eur"),
        "was_price_eur": None,
        "claimed_discount": None,
        "valid_until": match.parse_valid_until(o.get("valid_until") or "") or (o.get("valid_until") or None),
        "url": o.get("url") or "",
        "heat": None,
        "raw": o.get("evidence") or "",
        "sku": sku,
        "sku_class": cfg.get("class"),
        "match_conf": "high",
    }
    if cfg.get("class") == "consumable":
        qty, unit = match.to_base(o.get("pack_qty"), o.get("pack_unit"))
        expected_unit = cfg.get("unit")
        if unit is not None and expected_unit is not None and unit != expected_unit:
            qty, unit = None, None
        offer["qty"] = qty
        offer["unit"] = unit
        offer["pending_qty"] = qty is None
        price = offer["price_eur"]
        offer["unit_price_eur"] = round(price / qty, 4) if (price is not None and qty) else None
    else:
        offer["qty"] = None
        offer["unit"] = None
        offer["unit_price_eur"] = None
        offer["pending_qty"] = False
    return offer


# ── Stage 4: AUDIT ───────────────────────────────────────────────────────────

def _audit_lead_payload(c):
    sku_cfg = catalog.CATALOG.get(c.get("sku")) or {}
    return {
        "lead_id": c.get("lead_id"),
        "name": c.get("name"),
        "retailer": c.get("retailer"),
        "source": c.get("source"),
        "sku": c.get("sku"),
        "sku_class": c.get("sku_class"),
        "price_eur": c.get("price_eur"),
        "was_price_eur": c.get("was_price_eur"),
        "claimed_discount": c.get("claimed_discount"),
        "pack_qty_known": c.get("qty"),
        "pack_unit_known": c.get("unit"),
        "pending_qty": c.get("pending_qty"),
        "valid_until": c.get("valid_until"),
        "url": c.get("url"),
        "target_eur": sku_cfg.get("target_eur"),
    }


def _apply_audit(cand, v):
    if cand.get("pending_qty") and cand.get("sku_class") == "consumable":
        pq, pu = v.get("pack_qty"), v.get("pack_unit")
        qty, unit = (match.to_base(pq, pu) if (pq is not None and pu) else (None, None))
        expected_unit = match.unit_of(cand.get("sku"))
        if unit is not None and expected_unit is not None and unit != expected_unit:
            qty, unit = None, None
        if qty and cand.get("price_eur") is not None:
            cand["qty"] = qty
            cand["unit"] = unit
            cand["unit_price_eur"] = round(cand["price_eur"] / qty, 4)
            cand["pending_qty"] = False

    cand["fit_score"] = v.get("fit_score")
    cand["reference_price_eur"] = v.get("reference_price_eur")
    cand["ref_confidence"] = v.get("ref_confidence")
    cand["ref_comparators"] = v.get("ref_comparators", "")
    cand["trap_detected"] = v.get("trap_detected", "none")
    cand["quality_flag"] = v.get("quality_flag", "ok")
    cand["action_note"] = v.get("action_note", "")
    cand["storage_note"] = v.get("storage_note", "")

    cand["quarantine"] = bool(cand.get("sku_class") == "consumable" and cand.get("unit_price_eur") is None)


# ── Stage 5: CORROBORATE ─────────────────────────────────────────────────────

def _corroborate_lead_payload(c):
    return {
        "lead_id": c.get("lead_id"),
        "name": c.get("name"),
        "retailer": c.get("retailer"),
        "sku": c.get("sku"),
        "price_eur": c.get("price_eur"),
        "was_price_eur": c.get("was_price_eur"),
        "reference_price_eur": c.get("reference_price_eur"),
        "url": c.get("url"),
    }


def _apply_corroboration(cand, v, hist):
    if v.get("direction") == "lowers":
        cand["reference_price_eur"] = v.get("reference_price_eur")
    listings = v.get("listings") or []
    if v.get("corroborated") and len(listings) >= C.CORROBORATE_MIN_LISTINGS:
        cand["corroborated"] = True
        cand["corroborate_n_listings"] = len(listings)
        ref_val = v.get("reference_price_eur")
        if cand.get("sku_class") == "consumable" and cand.get("qty"):
            reg_price = ref_val / cand["qty"] if isinstance(ref_val, (int, float)) else None
        else:
            reg_price = ref_val if isinstance(ref_val, (int, float)) else None
        if reg_price is not None:
            history.record_regular(hist, cand.get("sku"), reg_price, source="corroborate",
                                    note=v.get("evidence", ""))


# ── Stage 6: VERDICT ─────────────────────────────────────────────────────────

def _evidence_legs(cand, hist, sku_cfg, ref_level=None):
    legs = set()
    # The reference level IS the evidence for a consumable. A statutory shelf price is
    # far stronger than the hand-set par this replaced, which is what keeps consumables
    # clearing MIN_EVIDENCE_FAIR without needing Stage-5 corroboration every week.
    # L3 (llm_reference) grants NO leg — an LLM-supplied number is never the authority.
    if ref_level == C.REF_OWN_SHELF:
        legs.add("own_shelf")
    elif ref_level == C.REF_CATEGORY_P25:
        legs.add("statutory_shelf")
    if cand.get("was_price_eur"):
        legs.add("retailer_claim")
    if cand.get("source") == "ccc" and cand.get("was_price_eur"):
        legs.add("ccc_was")
    if (cand.get("heat") or 0) >= C.MYDEALZ_HOT_DEGREES:
        legs.add("mydealz_hot")
    # regular_median and the shelf legs are computed from the SAME `regular` series, so
    # granting both double-counts one source: 1.0 + 1.0 = 2.0 clears MIN_EVIDENCE_STRONG
    # outright, and a single Lidl shelf history would be sufficient evidence all by
    # itself. The pair this replaced (user_par + regular_median) really were independent
    # — one was the catalog, the other was observation — so the sum was honest there and
    # is not here. Mutually exclusive, shelf leg wins: it is the more specific claim.
    if not legs & {"own_shelf", "statutory_shelf"}:
        rstats = (history.stats_for(hist, cand.get("sku")).get("regular") or {})
        if ((rstats.get("n") or 0) >= C.REGULAR_MIN_N
                and (rstats.get("span_days") or 0) >= C.REGULAR_MIN_SPAN_DAYS):
            legs.add("regular_median")
    if cand.get("corroborated"):
        legs.add("corroborated")
    if cand.get("trap_detected") in ("inflated_was_price", "recurring_evergreen_promo"):
        legs.discard("retailer_claim")
    return legs


def _score(cand, hist):
    sku = cand.get("sku")
    sku_cfg = catalog.CATALOG.get(sku) or {}

    # The reference is resolved BEFORE the evidence legs, because which level we landed
    # on is itself the strongest evidence a consumable has.
    baseline = history.baseline_stats(hist, sku)
    reference, ref_level, ref_conf = C.reference_for(cand, baseline)
    cand["reference_eur"] = reference
    cand["reference_level"] = ref_level
    cand["reference_confidence"] = ref_conf
    cand["baseline_n"] = baseline.get("n")
    cand["baseline_spread"] = baseline.get("spread")

    legs = _evidence_legs(cand, hist, sku_cfg, ref_level)
    evidence = C.ref_evidence(legs)
    cand["evidence_legs"] = sorted(legs)
    cand["evidence"] = evidence

    floor = C.promo_floor(history.stats_for(hist, sku))

    # bulk_qty and saving_eur are computed BEFORE the verdict, because the stock-up floor
    # is now one of the gates. Left in the old order (saving_eur assigned at the end of
    # this function) the gate reads None on every single lead, so it never fires — and
    # the digest looks completely identical, with no error anywhere. saving_eur_for reads
    # reference_eur, unit_price_eur and bulk_qty; all three are set by this point.
    cand["bulk_qty"] = sku_cfg.get("bulk_qty")
    cand["saving_eur"] = C.saving_eur_for(cand)

    verdict, discount, failed = C.verdict_consumable(
        cand.get("unit_price_eur"), reference, ref_conf, floor,
        cand.get("fit_score"), evidence, sku_cfg.get("target_eur"),
        saving_eur=cand["saving_eur"])

    if cand.get("quality_flag") == "junk" and verdict == C.VERDICT_STRONG:
        verdict = C.VERDICT_SKIP
        failed = list(failed) + ["quality_flag"]

    cand["verdict"] = verdict
    cand["discount"] = discount
    cand["failed_gates"] = failed
    return cand


# ── Anti-spam (Stage 7) ──────────────────────────────────────────────────────

def load_seen():
    return X.load_json("seen.json", {"seen": {}})


def prune_seen(state):
    today = dt.date.today()
    kept = {}
    for k, rec in (state.get("seen") or {}).items():
        ttl = rec.get("ttl_days", C.DEFAULT_RESTOCK_DAYS)
        try:
            age = (today - dt.date.fromisoformat(rec.get("d", ""))).days
        except (TypeError, ValueError):
            age = 0
        if age <= ttl:
            kept[k] = rec
    state["seen"] = kept
    return state


def _cand_price(cand):
    return cand.get("unit_price_eur") if cand.get("sku_class") == "consumable" else cand.get("price_eur")


def _is_repeat(state, cand):
    if cand.get("sku") in C.FORCE_INCLUDE:
        return False
    rec = (state.get("seen") or {}).get(C.seen_key(cand))
    if not rec:
        return False
    prev_rank = C.VERDICT_RANK.get(rec.get("verdict"), 9)
    cur_rank = C.VERDICT_RANK.get(cand.get("verdict"), 9)
    if cur_rank < prev_rank:
        return False  # verdict upgrade re-notifies
    prev_price, cur_price = rec.get("price"), _cand_price(cand)
    if prev_price and cur_price is not None and prev_price > 0 and \
            (prev_price - cur_price) / prev_price >= C.PRICE_BREAKTHROUGH:
        return False  # a materially better price overrides suppression
    return True


def mark_seen(state, cand):
    sku_cfg = catalog.CATALOG.get(cand.get("sku")) or {}
    state.setdefault("seen", {})[C.seen_key(cand)] = {
        "d": X.today_iso(),
        "retailer": cand.get("retailer"),
        "verdict": cand.get("verdict"),
        "ttl_days": sku_cfg.get("restock_days", C.DEFAULT_RESTOCK_DAYS),
        "price": _cand_price(cand),
    }


def load_deals_history():
    return X.load_json("deals_history.json", {"entries": []})


def prune_deals_history(state):
    cutoff = (dt.date.today() - dt.timedelta(days=C.DEALS_HISTORY_MAX_DAYS)).isoformat()
    entries = [e for e in state.get("entries", []) if e.get("date", "") >= cutoff]
    state["entries"] = entries[-C.DEALS_HISTORY_MAX_ENTRIES:]
    return state


# ── Item rendering — ONE ordered list consumed by html / text / history ─────

ITEM_BLOCKS = [
    ("action_note", lambda c: c.get("action_note") or ""),
    ("storage_note", lambda c: c.get("storage_note") or ""),
]

# Retired audit prose. web/src/App.jsx still reads these six keys, and test_stub.py greps
# that file and asserts every `e.<field>` it reads is present in a deals_history entry.
# App.jsx is deliberately out of scope for this change, so the keys are emitted EMPTY:
# App.jsx guards each render with `&&`, so "" renders nothing instead of crashing.
# REMOVE THIS LIST when App.jsx is migrated to action_note / storage_note.
LEGACY_WEB_FIELDS = ["the_math", "about", "value_case", "market_insight",
                     "bulk_advice", "red_flags"]

# Plain-text badge for the card header. C.VERDICT_LABEL carries emoji ("✅ Strong Buy"),
# which is wrong inside a "[ ... ]" badge and unreliable in text/plain.
VERDICT_BADGE = {C.VERDICT_STRONG: "STRONG BUY", C.VERDICT_FAIR: "FAIR"}

# The STRUCTURED half of the item contract, as ITEM_BLOCKS is the prose half. The email
# renders numbers via _headline(), but web/ re-renders them itself and so needs them as
# fields: 15 of the 26 keys App.jsx reads used to be absent here, which left every card
# blank of price and score and made the sku_class filter match nothing.
#
# bulk_total_eur is computed HERE and not in App.jsx on purpose. Python owns all
# arithmetic (see CLAUDE.md), and App.jsx's bulk line wants the stock-up total while
# `price_eur` is the pack price — passing price_eur through would print
# "buy 5 kg = EUR 9.80" instead of EUR 49.00, wrong in a way that looks plausible.
ITEM_DATA_FIELDS = [
    ("sku_class", lambda c, cfg: c.get("sku_class")),
    ("label", lambda c, cfg: cfg.get("label")),
    ("unit", lambda c, cfg: c.get("unit") or cfg.get("unit")),
    ("qty", lambda c, cfg: cfg.get("bulk_qty")),
    ("unit_price_eur", lambda c, cfg: c.get("unit_price_eur")),
    ("price_eur", lambda c, cfg: c.get("price_eur")),
    ("bulk_total_eur", lambda c, cfg: _bulk_total(c, cfg)),
    # Which reference produced this verdict, and how much we trust it. Rendered in both
    # the email and web/ so every verdict is auditable — a discount is meaningless
    # without the number it was measured against.
    ("reference_eur", lambda c, cfg: c.get("reference_eur")),
    ("reference_level", lambda c, cfg: c.get("reference_level")),
    ("reference_confidence", lambda c, cfg: c.get("reference_confidence")),
    ("target_eur", lambda c, cfg: cfg.get("target_eur")),
    ("reference_price_eur", lambda c, cfg: c.get("reference_price_eur")),
    ("discount", lambda c, cfg: c.get("discount")),
    ("saving_eur", lambda c, cfg: c.get("saving_eur")),
    ("fit_score", lambda c, cfg: c.get("fit_score")),
    ("evidence", lambda c, cfg: c.get("evidence")),
    ("valid_until", lambda c, cfg: c.get("valid_until")),
    # How long the PRODUCT keeps (not restock_days, which is how long a stock-up LASTS
    # this household). Rendered in web/'s Drawer as "Keeps ~N days" — see CLAUDE.md.
    ("shelf_life_days", lambda c, cfg: cfg.get("shelf_life_days")),
]


# Read by _metrics() for the low-confidence caveat line: a low-confidence reference caps
# the verdict at Fair, so the digest says why rather than letting the number look more
# authoritative than it is.
REFERENCE_CAVEAT = {
    C.REF_CATEGORY_P25: "capped at Fair — this sku mixes product grades, so its p25 is "
                        "not like-for-like",
    C.REF_LLM:          "capped at Fair — no shelf price observed yet, so this reference "
                        "is an LLM estimate",
}


def _bulk_total(c, sku_cfg):
    """What a stock-up actually costs: unit price x bulk_qty. None unless both are known,
    so the UI omits the clause rather than printing a wrong total."""
    unit_price, bulk_qty = c.get("unit_price_eur"), sku_cfg.get("bulk_qty")
    if unit_price is None or not bulk_qty:
        return None
    return round(unit_price * bulk_qty, 2)


def _metrics(c):
    """The single site that formats every number in the email. Returns strings only, so
    the HTML and text renderers consume the same values and cannot quietly disagree."""
    sku_cfg = catalog.CATALOG.get(c.get("sku")) or {}
    unit = c.get("unit") or sku_cfg.get("unit") or "kg"
    bulk_qty = sku_cfg.get("bulk_qty")
    bulk_total = _bulk_total(c, sku_cfg)
    price_eur = c.get("price_eur")
    unit_price_eur = c.get("unit_price_eur")
    reference_eur = c.get("reference_eur")
    saving_eur = c.get("saving_eur")
    discount = c.get("discount")
    verdict = c.get("verdict")
    level = c.get("reference_level")
    low_conf = c.get("reference_confidence") == C.CONF_LOW

    if bulk_total is not None:
        deal_price, deal_price_sub = f"€{bulk_total:.2f}", f"for {bulk_qty:g} {unit}"
    elif price_eur is not None:
        deal_price, deal_price_sub = f"€{price_eur:.2f}", "per pack"
    else:
        deal_price, deal_price_sub = "—", ""

    unit_price = f"€{unit_price_eur:.2f} / {unit}" if unit_price_eur is not None else "—"

    savings_label = "EST. SAVINGS" if low_conf else "SAVINGS"
    savings = (f"€{saving_eur:.2f}" + ("*" if low_conf else "")) if saving_eur is not None else "—"

    if discount is not None and reference_eur is not None:
        savings_sub = f"{round(discount * 100)}% vs €{reference_eur:.2f}/{unit}"
    else:
        savings_sub = ""

    caveat = REFERENCE_CAVEAT.get(level, "") if low_conf else ""

    target_note = ""
    target_eur = sku_cfg.get("target_eur")
    if target_eur and unit_price_eur is not None and unit_price_eur <= target_eur:
        target_note = f"beats your €{target_eur:.2f}/{unit} target"

    return {
        "name": c.get("name") or sku_cfg.get("label") or c.get("sku") or "?",
        "retailer": c.get("retailer") or "—",
        "badge": VERDICT_BADGE.get(verdict, ""),
        "color": C.VERDICT_COLOR.get(verdict, "#333"),
        "valid": _fmt_date(c.get("valid_until")) or "Shelf stock",
        "deal_price": deal_price,
        "deal_price_sub": deal_price_sub,
        "unit_price": unit_price,
        "savings_label": savings_label,
        "savings": savings,
        "savings_sub": savings_sub,
        "caveat": caveat,
        "target_note": target_note,
        "url": c.get("url") or "",
        "action_note": c.get("action_note") or "",
        "storage_note": c.get("storage_note") or "",
    }


def _headline(c):
    m = _metrics(c)
    parts = [f"{m['name']} — {m['unit_price']}"]
    if m["deal_price"] != "—":
        parts.append(f"stock up {m['deal_price_sub']} = {m['deal_price']}, saves {m['savings']}")
    if m["savings_sub"]:
        parts.append(m["savings_sub"])
    if m["caveat"]:
        parts.append(m["caveat"])
    if m["target_note"]:
        parts.append(m["target_note"])
    return " · ".join(parts)


def _item_dict(c):
    sku_cfg = catalog.CATALOG.get(c.get("sku")) or {}
    d = {
        "sku": c.get("sku"), "name": c.get("name"), "retailer": c.get("retailer"),
        "verdict": c.get("verdict"), "url": c.get("url"),
        "is_repeat": bool(c.get("is_repeat")), "headline": _headline(c),
    }
    for key, fn in ITEM_BLOCKS:
        d[key] = fn(c)
    for key in LEGACY_WEB_FIELDS:
        d[key] = ""
    for key, fn in ITEM_DATA_FIELDS:
        d[key] = fn(c, sku_cfg)
    return d


def build_history_entries(items, today):
    entries = []
    for c in items:
        d = _item_dict(c)
        d["date"] = today
        entries.append(d)
    return entries


def _render_item_html(c):
    """Tier-1 card: badge, name, store/valid line, a metric box, the ≤2 note bullets
    (Invariant TWO-NOTES — enforced by iterating ITEM_BLOCKS, never naming a key), and a
    styled CTA button."""
    m = _metrics(c)
    cells = [
        ("DEAL PRICE", m["deal_price"], m["deal_price_sub"]),
        ("UNIT PRICE", m["unit_price"], ""),
        (m["savings_label"], m["savings"], m["savings_sub"]),
    ]
    cells_html = "".join(
        f"<td style='padding:8px;border:1px solid #dee2e6;text-align:center;width:33%;vertical-align:top'>"
        f"<div style='font-size:10px;color:#6c757d;letter-spacing:.5px'>{_esc(label)}</div>"
        f"<div style='font-size:14px;font-weight:bold;color:#212529'>{_esc(value)}</div>"
        f"<div style='font-size:11px;color:#6c757d'>{_esc(sub)}</div></td>"
        for label, value, sub in cells
    )
    lines = [
        "<div style='border:1px solid #dee2e6;border-radius:6px;padding:12px;margin:0 0 14px 0'>",
        f"<div style='font-size:12px;font-weight:bold;color:{m['color']}'>[ {_esc(m['badge'])} ]</div>",
        f"<div style='font-size:16px;font-weight:bold;margin:2px 0'>{_esc(m['name'])}</div>",
        f"<div style='font-size:12px;color:#6c757d;margin-bottom:8px'>Store: {_esc(m['retailer'])} | Valid: {_esc(m['valid'])}</div>",
        f"<table style='border-collapse:collapse;background:#f8f9fa;border:1px solid #dee2e6;width:100%'><tr>{cells_html}</tr></table>",
    ]
    if m["caveat"]:
        lines.append(f"<div style='font-size:11px;color:#8a6d00;margin-top:6px'>* {_esc(m['caveat'])}</div>")
    for i, (key, _fn) in enumerate(ITEM_BLOCKS):
        value = m.get(key)
        if not value:
            continue
        label = key.split("_")[0].upper()
        style = "font-size:13px;color:#333;margin-top:6px" if i == 0 else "font-size:13px;color:#333"
        lines.append(f"<div style='{style}'>&bull; <b>{label}:</b> {_esc(value)}</div>")
    if m["target_note"]:
        lines.append(f"<div style='font-size:12px;color:#0a7d2e;margin-top:4px'>{_esc(m['target_note'])}</div>")
    if m["url"]:
        lines.append(
            f"<a href='{_safe_url(m['url'])}' style='display:inline-block;background:{m['color']};"
            f"color:#ffffff;text-decoration:none;padding:10px 16px;border-radius:4px;"
            f"font-size:14px;font-weight:bold;margin-top:10px'>&#10142; BUY ON {_esc(m['retailer'].upper())}</a>"
        )
    lines.append("</div>")
    return "".join(lines)


def _render_item_text(c):
    """Tier-1 card, text/plain. Lines whose value is unknown ("—"/"") are omitted rather
    than printed empty."""
    m = _metrics(c)
    lines = [
        f"[ {m['badge']} ] {m['name']}",
        f"Store: {m['retailer']} | Valid: {m['valid']}",
    ]
    if m["deal_price"] != "—":
        lines.append(f"• Deal Price:   {m['deal_price']} ({m['deal_price_sub']})")
    if m["unit_price"] != "—":
        lines.append(f"• Unit Price:   {m['unit_price']}")
    savings_label_titled = "Est. Savings" if m["savings_label"] == "EST. SAVINGS" else "Savings"
    if m["savings"] != "—":
        sub = f" ({m['savings_sub']})" if m["savings_sub"] else ""
        lines.append(f"• {savings_label_titled}: {m['savings']}{sub}")
    if m["caveat"]:
        lines.append(f"* {m['caveat']}")
    if m["action_note"]:
        lines.append(f"• Action:       {m['action_note']}")
    if m["storage_note"]:
        lines.append(f"• Storage:      {m['storage_note']}")
    if m["target_note"]:
        lines.append(f"• {m['target_note']}")
    if m["url"]:
        lines.append(f"Link: {m['url']}")
    return "\n".join(lines)


def _tier2_pct(c):
    return round(c["discount"] * 100) if c.get("discount") is not None else None


def _render_tier2_text(c):
    m, pct = _metrics(c), _tier2_pct(c)
    line = f"[{m['badge']}] {m['name']} — {m['unit_price']}"
    if m["deal_price"] != "—":
        line += f" ({m['deal_price']} total)"
    if m["savings_sub"] and pct is not None:
        line += f" | Saves {pct}% @ {m['retailer']}"
    if c.get("is_repeat"):
        line += " (repeat)"
    if m["url"]:
        line += f" -> {m['url']}"
    return line


def _render_tier2_html(c):
    m, pct = _metrics(c), _tier2_pct(c)
    text = f"[{_esc(m['badge'])}] {_esc(m['name'])} — {_esc(m['unit_price'])}"
    if m["deal_price"] != "—":
        text += f" ({_esc(m['deal_price'])} total)"
    if m["savings_sub"] and pct is not None:
        text += f" | Saves {pct}% @ {_esc(m['retailer'])}"
    if c.get("is_repeat"):
        text += " (repeat)"
    if m["url"]:
        text += f" <a href='{_safe_url(m['url'])}'>view</a>"
    return f"<div style='font-size:13px;padding:3px 0;border-bottom:1px solid #f1f3f5'>{text}</div>"


def _dedupe_emailable(items):
    """Collapse to one item per (sku or lowercased name), keeping the highest rank_score.
    Ties break on first-seen order; input order is otherwise preserved for survivors."""
    best = {}
    order = []
    for c in items:
        key = c.get("sku") or (c.get("name") or "").strip().lower()
        if key not in best:
            best[key] = c
            order.append(key)
        elif (c.get("rank_score") or 0) > (best[key].get("rank_score") or 0):
            best[key] = c
    return [best[key] for key in order]


def _tier(emailable):
    """Partition the deduped emailable list into (tier1, tier2). Every item appears
    exactly once (Invariant ONE-PASS). Repeats are demoted to tier2, never hidden."""
    repeats = [c for c in emailable if c.get("is_repeat")]
    fresh = [c for c in emailable if not c.get("is_repeat")]
    strong = sorted((c for c in fresh if c["verdict"] == C.VERDICT_STRONG),
                     key=lambda c: -(c.get("rank_score") or 0))
    fair = sorted((c for c in fresh if c["verdict"] == C.VERDICT_FAIR),
                   key=lambda c: -(c.get("rank_score") or 0))
    tier1 = strong + fair[:max(0, C.TOP_N_BLOCK - len(strong))]
    tier2 = fair[max(0, C.TOP_N_BLOCK - len(strong)):] + sorted(
        repeats, key=lambda c: -(c.get("rank_score") or 0))
    return tier1, tier2


def _reject_summary_lines(rejects):
    """Group rejects by reason, most-frequent first, capped at C.MAX_REJECT_SUMMARY_LINES
    lines rather than one line per reject."""
    groups = {}
    order = []
    for o in rejects:
        reason = o.get("reject_reason")
        if reason not in groups:
            groups[reason] = []
            order.append(reason)
        groups[reason].append(o)

    reasons_sorted = sorted(order, key=lambda r: (-len(groups[r]), r or ""))
    lines = []
    for reason in reasons_sorted[:C.MAX_REJECT_SUMMARY_LINES]:
        items = groups[reason]
        n = len(items)
        if n == 1:
            o = items[0]
            name = o.get("name") or o.get("sku") or "?"
            if reason == "over_reference" and o.get("sku_class") == "consumable":
                sku_cfg = catalog.CATALOG.get(o.get("sku")) or {}
                ref, up = o.get("reference_eur"), o.get("unit_price_eur")
                unit = o.get("unit") or sku_cfg.get("unit") or "kg"
                if up is not None and ref is not None:
                    lines.append(f"1 x over_reference: {name} (€{up:.2f}/{unit} vs €{ref:.2f}/{unit} shelf)")
                    continue
            lines.append(f"1 x {reason}: {name}")
        else:
            names = []
            for o in items[:3]:
                name = o.get("name") or o.get("sku") or "?"
                if len(name) > 28:
                    name = name[:28] + "…"
                names.append(name)
            examples = ", ".join(names)
            if n > 3:
                examples += ", etc."
            lines.append(f"{n} x {reason} ({examples})")
    if len(reasons_sorted) > C.MAX_REJECT_SUMMARY_LINES:
        lines.append(f"... and {len(reasons_sorted) - C.MAX_REJECT_SUMMARY_LINES} more reason(s)")
    return lines


def _source_report_line(reports):
    parts = []
    for r in reports:
        part = f"{r.get('source')}: {'OK' if r.get('ok') else 'FAILED'} (n={r.get('n')})"
        if not r.get("ok"):
            part += f" — {r.get('note')}"
        parts.append(part)
    return " | ".join(parts)


def maintenance_lines(hist):
    """-> list[str]. Skus whose shelf-price spread is too wide for their p25 to mean
    anything — the "split this sku" to-do list.

    This is the visible half of the L2 reference, and it is the direct successor to the
    par-review block. The reasoning is the same shape: `verdict_consumable` handles a
    wide-spread sku SAFELY (it caps at Fair) and therefore SILENTLY. `food.rice` mixing
    €1.53 plain white with €3.49 risotto and pearl grades is not a market fact, it is a
    catalog problem, and nothing errors while it persists — the sku just quietly never
    reaches Strong Buy. `food.pasta` was the worst case at €1.98 durum against €47.67
    boutique, and was removed from the catalog for exactly that reason.

    Splitting a sku is a human decision, so this reports and changes nothing. Measured
    2026-07-31: 18 of 27 observed skus are over the threshold, which is a finite,
    shrinking to-do list rather than an unbounded warning stream.

    Reads the WINDOWED regular series only — the same series the reference reads, so the
    line always describes the number the verdicts actually used."""
    lines = []
    for sku, cfg in sorted(catalog.CATALOG.items()):
        if cfg.get("class") != "consumable":
            continue
        b = history.baseline_stats(hist, sku)
        spread = b.get("spread")
        if (b.get("n") or 0) < C.REGULAR_MIN_N or not isinstance(spread, (int, float)):
            continue
        if spread <= C.BASELINE_MAX_SPREAD:
            continue
        unit = cfg.get("unit") or "kg"
        lines.append(
            f"{sku}: shelf prices run €{b['p10']:.2f}–€{b['p90']:.2f}/{unit} "
            f"({spread:.1f}x spread, n={b['n']}), so its €{b['p25']:.2f}/{unit} p25 is "
            f"not like-for-like — capped at Fair until the sku is split."
        )
    return lines


_CAVEATS_HTML = (
    "<div style='font-size:11px;color:#bbb;margin-top:16px'>"
    "Lidl prices are for Plovdiv-area stores only. Discovered leads come from live web "
    "search and may need reconfirming at checkout. A quiet week is a correct outcome, "
    "not a bug.</div>"
)
_CAVEATS_TEXT = (
    "Caveats: Lidl prices are for Plovdiv-area stores only. Discovered leads come from "
    "live web search and may need reconfirming at checkout. A quiet week is a correct "
    "outcome, not a bug."
)


def build_email_html(subject, tier1, tier2, rejects, reports, stale, today, maintenance=()):
    n_strong = sum(1 for c in tier1 + tier2 if c["verdict"] == C.VERDICT_STRONG)
    n_fair = sum(1 for c in tier1 + tier2 if c["verdict"] == C.VERDICT_FAIR)

    tier1_html = "".join(_render_item_html(c) for c in tier1)

    tier2_block = ""
    if tier2:
        tier2_html = "".join(_render_tier2_html(c) for c in tier2)
        tier2_block = f"<h3 style='font-size:14px'>OTHER QUALIFIED DEALS ({len(tier2)})</h3>{tier2_html}"

    reject_html = "".join(
        f"<div style='font-size:12px;color:#999'>{_esc(l)}</div>" for l in _reject_summary_lines(rejects)
    )

    maint_lines = list(maintenance[:C.MAX_MAINTENANCE_LINES])
    if len(maintenance) > C.MAX_MAINTENANCE_LINES:
        maint_lines.append(f"... and {len(maintenance) - C.MAX_MAINTENANCE_LINES} more")
    if stale:
        maint_lines.append(f"stale (no match in {C.CATALOG_STALE_RUNS}+ runs): {', '.join(stale)}")
    maint_block = ""
    if maint_lines:
        maint_html = "".join(f"<div style='font-size:12px;color:#a15c00'>{_esc(l)}</div>" for l in maint_lines)
        maint_block = f"<h4>Catalog maintenance</h4>{maint_html}"

    source_html = f"<div style='font-size:12px;color:#999'>{_esc(_source_report_line(reports))}</div>"

    return (
        f"<div style='font-family:system-ui,sans-serif;max-width:640px;padding:8px'>"
        f"<h2 style='margin:0 0 2px 0;font-size:18px'>WEEKLY SHOPPING HUNT | {_esc(today)}</h2>"
        f"<div style='font-size:13px;color:#6c757d;margin-bottom:14px'>"
        f"Summary: {n_strong} Strong Buy &middot; {n_fair} Fair &middot; {len(rejects)} Rejected</div>"
        f"<h3 style='font-size:14px'>TOP DEALS ({len(tier1)})</h3>{tier1_html}"
        f"{tier2_block}"
        f"<h3 style='font-size:14px'>SYSTEM LOGS &amp; PIPELINE HEALTH</h3>"
        f"<h4>REJECTED LEADS ({len(rejects)} total)</h4>{reject_html}"
        f"<h4>CATALOG WARNINGS</h4>{maint_block}"
        f"<h4>SOURCE STATUS</h4>{source_html}"
        f"{_CAVEATS_HTML}"
        f"</div>"
    )


def build_email_text(subject, tier1, tier2, rejects, reports, stale, today, maintenance=()):
    n_strong = sum(1 for c in tier1 + tier2 if c["verdict"] == C.VERDICT_STRONG)
    n_fair = sum(1 for c in tier1 + tier2 if c["verdict"] == C.VERDICT_FAIR)

    parts = [
        f"WEEKLY SHOPPING HUNT | {today}",
        f"Summary: {n_strong} Strong Buy · {n_fair} Fair · {len(rejects)} Rejected",
        "",
        f"TOP DEALS ({len(tier1)})",
    ]
    for c in tier1:
        parts.append(_render_item_text(c))
        parts.append("")

    if tier2:
        parts.append(f"OTHER QUALIFIED DEALS ({len(tier2)})")
        parts += [_render_tier2_text(c) for c in tier2]
        parts.append("")

    parts.append(f"{'=' * 20} SYSTEM LOGS & PIPELINE HEALTH {'=' * 20}")
    parts.append(f"REJECTED LEADS ({len(rejects)} total)")
    parts += _reject_summary_lines(rejects)

    maint_lines = list(maintenance[:C.MAX_MAINTENANCE_LINES])
    if len(maintenance) > C.MAX_MAINTENANCE_LINES:
        maint_lines.append(f"... and {len(maintenance) - C.MAX_MAINTENANCE_LINES} more")
    if stale:
        maint_lines.append(f"stale (no match in {C.CATALOG_STALE_RUNS}+ runs): {', '.join(stale)}")
    parts.append("CATALOG WARNINGS")
    if maint_lines:
        parts.append("Catalog maintenance")
        parts += maint_lines

    parts.append("SOURCE STATUS")
    parts.append(_source_report_line(reports))

    parts.append("\n" + _CAVEATS_TEXT)
    return "\n".join(parts)


def write_run_md(today, reports, audited_candidates, stale, n_strong, n_fair, n_emailed):
    lines = [f"# Shop Hunter Run — {today}", "",
             f"Strong Buy: {n_strong} · Fair: {n_fair} · Emailed: {n_emailed}", "",
             "## Source report"]
    for r in reports:
        lines.append(f"- {r.get('source')}: {'FAILED' if not r.get('ok') else 'ok'} "
                      f"n={r.get('n')} {r.get('note') or ''}")
    lines.append("")
    lines.append("## Evaluated leads")
    for c in audited_candidates:
        badge = C.VERDICT_LABEL.get(c.get("verdict"), c.get("verdict"))
        lines.append(f"- {badge} — {c.get('name')} ({c.get('sku')}) failed={c.get('failed_gates')}")
    lines.append("")
    if stale:
        lines.append("## Catalog health (stale)")
        for s in stale:
            lines.append(f"- {s}")
    with open(os.path.join(X.STATE_DIR, "run.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    today = X.today_iso()
    _section(f"SHOP HUNTER · {today} · provider={X.PROVIDER}")

    _section("STAGE 0 · HARVEST")
    offers, reports, regular_rows = sources.harvest()
    print(f"  {len(offers)} offer(s) from {len(reports)} source(s); "
          f"{len(regular_rows or [])} Lidl regular row(s)")
    for r in reports:
        if not r.get("ok"):
            print(f"  [FAILED SOURCE] {r.get('source')}: {r.get('note')}")

    _section("STAGE 1 · NORMALISE + MATCH")
    matched = sum(1 for o in offers if o.get("sku"))
    pending = sum(1 for o in offers if o.get("pending_qty"))
    rate = round(100 * matched / len(offers)) if offers else 0
    print(f"  {matched}/{len(offers)} matched a catalog sku ({rate}%); {pending} pending_qty")

    hist = history.load()
    led = history.load_ledger()
    health = history.load_health()

    _section("STAGE 1b · RECORD LIDL REGULARS")
    n_reg = _record_lidl_regulars(hist, regular_rows)
    print(f"  {n_reg} Lidl regular-price row(s) recorded into the regular series")

    _section("STAGE 2 · PREFILTER")
    baselines = {sku: history.baseline_stats(hist, sku) for sku in catalog.CATALOG}
    stage2_candidates, stage2_rejects, stage2_stats = prefilter.prefilter(offers, today, baselines)
    print(f"  {stage2_stats['n_out']} kept of {stage2_stats['n_in']}; "
          f"rejects_by_reason={stage2_stats['rejects_by_reason']}")

    if "--dry-run" in sys.argv[1:]:
        print("\n  --dry-run: stopping before any LLM call.")
        return

    _section("STAGE 3 · DISCOVER (search)")
    matched_skus_now = {o.get("sku") for o in offers if o.get("sku")}
    gap_text = _gap_skus_text(matched_skus_now)
    discover_offers_raw = []
    try:
        prompt3 = C.DISCOVER_PROMPT.format(
            today=today, household=C.HOUSEHOLD_CONTEXT, gap_skus=gap_text,
            always_check=_ALWAYS_CHECK_TEXT, memory=history.summarize_for_prompt(hist))
        raw3 = X.llm(messages=[{"role": "user", "content": prompt3}], model=C.MODEL_DISCOVER,
                     max_tokens=C.MAX_TOKENS_DISCOVER, want_search=True,
                     response_schema=C.STAGE_DISCOVER_SCHEMA, provider=C.PROVIDER_DISCOVER)
        discover_offers_raw = (X.parse_json_block(raw3) or {}).get("offers", [])
    except Exception as e:
        print(f"  [FAIL] Stage 3 LLM/parse error: {type(e).__name__}: {e} — 0 discover offers")
    if not isinstance(discover_offers_raw, list):
        discover_offers_raw = []

    discover_offers = []
    for o in discover_offers_raw:
        if not isinstance(o, dict):
            continue
        cfg = catalog.CATALOG.get(o.get("sku"))
        price = o.get("price_eur")
        if not cfg or not isinstance(price, (int, float)) or price <= 0:
            continue
        discover_offers.append(_offer_from_discover(o, o.get("sku"), cfg))
    print(f"  {len(discover_offers_raw)} lead(s) returned -> {len(discover_offers)} valid "
          f"(known sku + positive price)")

    discover_candidates, discover_rejects, discover_stats = prefilter.prefilter(
        discover_offers, today, baselines)
    print(f"  prefilter: {discover_stats['n_out']} kept of {discover_stats['n_in']}")

    all_candidates = stage2_candidates + discover_candidates
    for i, c in enumerate(all_candidates, 1):
        c["lead_id"] = i
    audited_candidates = all_candidates

    _section("STAGE 4 · AUDIT")
    if audited_candidates:
        mem_text = history.summarize_for_prompt(hist)
        gates_text = C.gates_prompt_text()
        for i in range(0, len(audited_candidates), C.AUDIT_BATCH_SIZE):
            batch = audited_candidates[i:i + C.AUDIT_BATCH_SIZE]
            verdicts4 = []
            try:
                payload = [_audit_lead_payload(c) for c in batch]
                prompt4 = C.AUDIT_PROMPT.format(
                    today=today, household=C.HOUSEHOLD_CONTEXT, gates=gates_text,
                    memory=mem_text, leads=json.dumps(payload, ensure_ascii=False, indent=2))
                raw4 = X.llm(messages=[{"role": "user", "content": prompt4}], model=C.MODEL_AUDIT,
                             max_tokens=C.MAX_TOKENS_AUDIT, want_search=False,
                             response_schema=C.STAGE_AUDIT_SCHEMA, provider=C.PROVIDER_AUDIT)
                verdicts4 = X.parse_json_block(raw4) or []
            except Exception as e:
                print(f"  [FAIL] Stage 4 batch {i // C.AUDIT_BATCH_SIZE + 1} "
                      f"LLM/parse error: {type(e).__name__}: {e}")
            if not isinstance(verdicts4, list):
                verdicts4 = []
            for v in verdicts4:
                if not isinstance(v, dict):
                    continue
                cand = _match_by_lead_id(batch, v.get("lead_id"))
                if not cand:
                    print(f"  [audit WARNING] lead_id {v.get('lead_id')!r} matched no candidate")
                    continue
                _apply_audit(cand, v)
        print(f"  audited {len(audited_candidates)} lead(s)")
    else:
        print("  nothing to audit")

    _section("STAGE 5 · CORROBORATE (search, gated)")
    for c in audited_candidates:
        _score(c, hist)  # prelim pass so evidence-only failures can be found
    gap_candidates = [c for c in audited_candidates if c.get("failed_gates") == ["evidence"]]
    gap_candidates.sort(key=lambda c: c.get("discount") or 0, reverse=True)
    to_corroborate = gap_candidates[:C.MAX_CORROBORATE_PER_RUN]
    print(f"  {len(gap_candidates)} lead(s) missing only their evidence bar; "
          f"corroborating {len(to_corroborate)} (cap {C.MAX_CORROBORATE_PER_RUN})")
    if to_corroborate:
        verdicts5 = []
        try:
            payload5 = [_corroborate_lead_payload(c) for c in to_corroborate]
            prompt5 = C.CORROBORATE_PROMPT.format(
                today=today, household=C.HOUSEHOLD_CONTEXT,
                min_listings=C.CORROBORATE_MIN_LISTINGS,
                leads=json.dumps(payload5, ensure_ascii=False, indent=2))
            raw5 = X.llm(messages=[{"role": "user", "content": prompt5}], model=C.MODEL_CORROBORATE,
                         max_tokens=C.MAX_TOKENS_CORROBORATE, want_search=True,
                         response_schema=C.STAGE_CORROBORATE_SCHEMA, provider=C.PROVIDER_CORROBORATE)
            verdicts5 = X.parse_json_block(raw5) or []
        except Exception as e:
            print(f"  [FAIL] Stage 5 LLM/parse error: {type(e).__name__}: {e}")
        if not isinstance(verdicts5, list):
            verdicts5 = []
        for v in verdicts5:
            if not isinstance(v, dict):
                continue
            cand = _match_by_lead_id(to_corroborate, v.get("lead_id"))
            if not cand:
                print(f"  [corroborate WARNING] lead_id {v.get('lead_id')!r} matched no candidate")
                continue
            _apply_corroboration(cand, v, hist)

    _section("STAGE 6 · VERDICT")
    for c in audited_candidates:
        _score(c, hist)  # forced re-score, reflects any Stage-5 change
    raw_strong = sum(1 for c in audited_candidates if c["verdict"] == C.VERDICT_STRONG)
    raw_fair = sum(1 for c in audited_candidates if c["verdict"] == C.VERDICT_FAIR)
    n_skip = len(audited_candidates) - raw_strong - raw_fair
    print(f"  {raw_strong} Strong Buy · {raw_fair} Fair · {n_skip} Skip")

    _section("STAGE 7 · DIGEST + STATE")
    seen_state = prune_seen(load_seen())
    for c in audited_candidates:
        c["is_repeat"] = _is_repeat(seen_state, c)
        c["rank_score"] = C.rank_score(
            c.get("discount"), c.get("saving_eur"), c.get("verdict"), c["is_repeat"],
            shelf_life_days=(catalog.CATALOG.get(c.get("sku")) or {}).get("shelf_life_days"))

    # Duplicates collapse here, at the single seam feeding the digest — write_run_md's
    # Strong/Fair counts below now reflect the deduped set, and a dropped duplicate is
    # never passed to mark_seen (safe: the seen key is the sku, not the retailer, so the
    # surviving twin marks the same key).
    emailable = _dedupe_emailable(
        [c for c in audited_candidates if c.get("verdict") in (C.VERDICT_STRONG, C.VERDICT_FAIR)])

    n_strong = sum(1 for c in emailable if c["verdict"] == C.VERDICT_STRONG)
    n_fair = sum(1 for c in emailable if c["verdict"] == C.VERDICT_FAIR)

    failed_hist = {}
    for c in audited_candidates:
        history.record_outcome(led, c)
        for g in c.get("failed_gates") or []:
            failed_hist[g] = failed_hist.get(g, 0) + 1
    for o in stage2_rejects + discover_rejects:
        if o.get("sku"):
            history.record_outcome(led, o)

    n_promo = 0
    for o in stage2_rejects + discover_rejects + audited_candidates:
        if o.get("sku") and history.record_promo(hist, o):
            n_promo += 1
    print(f"  {n_promo} promo observation(s) recorded")

    matched_skus_all = matched_skus_now | {c.get("sku") for c in audited_candidates if c.get("sku")}
    health = history.bump_health(health, matched_skus_all, catalog.CATALOG.keys())
    stale = history.stale_skus(health)

    # Computed AFTER the Stage 5 record_regular calls above, so a par gap that
    # corroboration just proved shows up in this week's email rather than next week's.
    maintenance = maintenance_lines(hist)
    if maintenance:
        print(f"  {len(maintenance)} sku(s) mix product grades — see the digest's Maintenance block")

    sent_items = []
    if n_strong + n_fair >= C.MIN_ITEMS_TO_EMAIL:
        subject = f"Weekly Shopping Hunt — {n_strong} Strong Buy · {n_fair} Fair · {today}"
        tier1, tier2 = _tier(emailable)
        html_body = build_email_html(subject, tier1, tier2, stage2_rejects + discover_rejects,
                                      reports, stale, today, maintenance)
        text_body = build_email_text(subject, tier1, tier2, stage2_rejects + discover_rejects,
                                      reports, stale, today, maintenance)
        if C.DRY_RUN:
            print(f"  [DRY RUN] would send: {subject}")
        else:
            try:
                X.send_email(subject, html_body, text_body)
                sent_items = emailable
                for c in emailable:
                    mark_seen(seen_state, c)
                print(f"  [EMAIL SENT] {subject}")
            except Exception as e:
                print(f"  [FAIL] email send error: {type(e).__name__}: {e} (seen state not marked)")
    else:
        print(f"  {n_strong} Strong Buy + {n_fair} Fair < MIN_ITEMS_TO_EMAIL "
              f"({C.MIN_ITEMS_TO_EMAIL}) — no email")

    deals_hist = load_deals_history()
    if sent_items:
        deals_hist["entries"].extend(build_history_entries(sent_items, today))
    deals_hist = prune_deals_history(deals_hist)
    X.save_json("deals_history.json", deals_hist)

    history.prune(hist)
    history.save(hist)
    history.prune_ledger(led)
    history.save_ledger(led)
    history.save_health(health)
    X.save_json("seen.json", seen_state)

    X.save_json("last_run.json", {
        "date": today,
        "reports": reports,
        "stage_counts": {
            "harvested": len(offers),
            "prefiltered_in": stage2_stats["n_in"], "prefiltered_out": stage2_stats["n_out"],
            "discovered": len(discover_offers),
            "audited": len(audited_candidates),
            "strong_buy": n_strong, "fair": n_fair, "skip": n_skip,
            "emailed": len(sent_items),
        },
        "rejects_by_reason": stage2_stats["rejects_by_reason"],
        "failed_gates": failed_hist,
        "stale_skus": stale,
        # Non-empty when a reasoning model was unavailable and a weaker one served the
        # stage. A fallback week's fit_scores are not strictly comparable to a normal
        # week's, so this is recorded rather than inferred from odd-looking output.
        "model_fallbacks": list(X.MODEL_FALLBACKS_USED),
    })
    write_run_md(today, reports, audited_candidates, stale, n_strong, n_fair, len(sent_items))

    _section("RUN COMPLETE")
    print(f"  {len(offers)} harvested -> {stage2_stats['n_out']} prefiltered -> "
          f"{len(discover_offers)} discovered -> {len(audited_candidates)} audited -> "
          f"{n_strong} Strong Buy / {n_fair} Fair -> {len(sent_items)} emailed")


if __name__ == "__main__":
    main()
