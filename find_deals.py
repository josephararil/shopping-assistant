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
performs arithmetic, promo/regular series never mix, quarantine != skip, and off-list
discovery can never exceed Fair.

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
    just stays absent — which is exactly why it is asserted in test_stub.py."""
    n = 0
    for row in regular_rows or []:
        synth = {"name": row.get("name"), "price_eur": row.get("price_eur")}
        match.annotate(synth)
        sku = synth.get("sku")
        unit_price = synth.get("unit_price_eur")
        if not sku or sku not in catalog.CATALOG or unit_price is None:
            continue
        if history.record_regular(hist, sku, unit_price, source="lidl_regular"):
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
        f"{sku} — {cfg.get('label', '')} (target €{cfg.get('par_eur')}/{cfg.get('unit')})"
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
        "category_hint": None,
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
        "on_list": c.get("on_list"),
        "price_eur": c.get("price_eur"),
        "was_price_eur": c.get("was_price_eur"),
        "claimed_discount": c.get("claimed_discount"),
        "pack_qty_known": c.get("qty"),
        "pack_unit_known": c.get("unit"),
        "pending_qty": c.get("pending_qty"),
        "valid_until": c.get("valid_until"),
        "url": c.get("url"),
        "par_eur": sku_cfg.get("par_eur"),
        "trigger_eur": sku_cfg.get("trigger_eur"),
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
    cand["the_math"] = v.get("the_math", "")
    cand["about"] = v.get("about", "")
    cand["value_case"] = v.get("value_case", "")
    cand["market_insight"] = v.get("market_insight", "")
    cand["bulk_advice"] = v.get("bulk_advice", "")
    cand["red_flags"] = v.get("red_flags", "")

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

def _evidence_legs(cand, hist, sku_cfg):
    legs = set()
    if sku_cfg.get("par_eur"):
        legs.add("user_par")
    if cand.get("was_price_eur"):
        legs.add("retailer_claim")
    if cand.get("source") == "ccc" and cand.get("was_price_eur"):
        legs.add("ccc_was")
    if (cand.get("heat") or 0) >= C.MYDEALZ_HOT_DEGREES:
        legs.add("mydealz_hot")
    rstats = (history.stats_for(hist, cand.get("sku")).get("regular") or {})
    if (rstats.get("n") or 0) >= C.REGULAR_MIN_N and (rstats.get("span_days") or 0) >= C.REGULAR_MIN_SPAN_DAYS:
        legs.add("regular_median")
    if cand.get("corroborated"):
        legs.add("corroborated")
    if cand.get("trap_detected") in ("inflated_was_price", "recurring_evergreen_promo"):
        legs.discard("retailer_claim")
    return legs


def _score(cand, hist):
    sku = cand.get("sku")
    sku_cfg = catalog.CATALOG.get(sku) or {}
    legs = _evidence_legs(cand, hist, sku_cfg)
    evidence = C.ref_evidence(legs)
    cand["evidence_legs"] = sorted(legs)
    cand["evidence"] = evidence

    if cand.get("sku_class") == "consumable":
        stats = history.stats_for(hist, sku)
        par, drift = C.effective_par(sku_cfg, stats)
        floor = C.promo_floor(stats)
        verdict, discount, failed = C.verdict_consumable(
            cand.get("unit_price_eur"), par, floor, cand.get("fit_score"), evidence)
        cand["par_eur"] = par
        cand["par_drift"] = drift
        cand["bulk_qty"] = sku_cfg.get("bulk_qty")
    else:
        verdict, discount, failed = C.verdict_durable(
            cand.get("price_eur"), sku_cfg.get("trigger_eur"), cand.get("reference_price_eur"),
            evidence, cand.get("fit_score"), cand.get("on_list", True))

    if cand.get("quality_flag") == "junk" and verdict == C.VERDICT_STRONG:
        verdict = C.VERDICT_SKIP
        failed = list(failed) + ["quality_flag"]

    cand["verdict"] = verdict
    cand["discount"] = discount
    cand["failed_gates"] = failed
    cand["saving_eur"] = C.saving_eur_for(cand)
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


def _top5(emailable):
    fresh = [c for c in emailable if not c.get("is_repeat")]
    fresh.sort(key=lambda c: -(c.get("rank_score") or 0))
    return fresh[:C.TOP_N_BLOCK]


# ── Item rendering — ONE ordered list consumed by html / text / history ─────

ITEM_BLOCKS = [
    ("about", lambda c: c.get("about") or ""),
    ("value_case", lambda c: c.get("value_case") or ""),
    # the_math is the audit's headline arithmetic — perceived vs actual saving. It is a
    # required field of STAGE_AUDIT_SCHEMA and the prompt spends a worked example on it,
    # so leaving it out of this list paid for it every run and rendered it nowhere.
    ("the_math", lambda c: c.get("the_math") or ""),
    ("market_insight", lambda c: c.get("market_insight") or ""),
    ("bulk_advice", lambda c: c.get("bulk_advice") or ""),
    ("red_flags", lambda c: c.get("red_flags") or ""),
]

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
    ("par_eur", lambda c, cfg: c.get("par_eur")),
    ("trigger_eur", lambda c, cfg: cfg.get("trigger_eur")),
    ("reference_price_eur", lambda c, cfg: c.get("reference_price_eur")),
    ("discount", lambda c, cfg: c.get("discount")),
    ("saving_eur", lambda c, cfg: c.get("saving_eur")),
    ("fit_score", lambda c, cfg: c.get("fit_score")),
    ("evidence", lambda c, cfg: c.get("evidence")),
    ("valid_until", lambda c, cfg: c.get("valid_until")),
]


def _bulk_total(c, sku_cfg):
    """What a stock-up actually costs: unit price x bulk_qty. None unless both are known,
    so the UI omits the clause rather than printing a wrong total."""
    unit_price, bulk_qty = c.get("unit_price_eur"), sku_cfg.get("bulk_qty")
    if unit_price is None or not bulk_qty:
        return None
    return round(unit_price * bulk_qty, 2)


def _consumable_line(c):
    sku_cfg = catalog.CATALOG.get(c.get("sku")) or {}
    name = c.get("name") or sku_cfg.get("label") or c.get("sku") or "?"
    unit = c.get("unit") or sku_cfg.get("unit") or "kg"
    unit_price, par, discount = c.get("unit_price_eur"), c.get("par_eur"), c.get("discount")
    bulk_qty, saving = sku_cfg.get("bulk_qty"), c.get("saving_eur")

    if unit_price is not None and par is not None:
        pct = f"{round(discount * 100)}% under" if discount is not None else "?"
        head = f"{name} — €{unit_price:.2f}/{unit} vs €{par:.2f} par ({pct})"
    elif unit_price is not None:
        head = f"{name} — €{unit_price:.2f}/{unit}"
    else:
        head = f"{name} — €?/{unit}"
    parts = [head]
    if bulk_qty and unit_price is not None and saving is not None:
        parts.append(f"buy {bulk_qty:g} {unit} = €{unit_price * bulk_qty:.2f}, saves €{saving:.2f}")
    if sku_cfg.get("bulk_note"):
        parts.append(sku_cfg["bulk_note"].rstrip("."))
    if c.get("valid_until"):
        parts.append(f"valid to {_fmt_date(c['valid_until'])}")
    return " · ".join(parts)


def _durable_line(c):
    sku_cfg = catalog.CATALOG.get(c.get("sku")) or {}
    name = c.get("name") or sku_cfg.get("label") or c.get("sku") or "?"
    price, ref, discount, saving = c.get("price_eur"), c.get("reference_price_eur"), c.get("discount"), c.get("saving_eur")
    trigger = sku_cfg.get("trigger_eur")

    price_str = f"€{price:.0f}" if isinstance(price, (int, float)) else "€?"
    head = f"{name} — {price_str}"
    if isinstance(ref, (int, float)) and isinstance(discount, (int, float)) and isinstance(saving, (int, float)):
        head += f" (normal €{ref:.0f}, {round(discount * 100)}% off, saves €{saving:.0f})"
    parts = [head]
    if trigger:
        parts.append(f"your trigger was €{trigger:.0f}")
    n_listings = c.get("corroborate_n_listings")
    if n_listings:
        parts.append(f"corroborated: {n_listings} current listings")
    if c.get("valid_until"):
        parts.append(f"valid to {_fmt_date(c['valid_until'])}")
    return " · ".join(parts)


def _headline(c):
    line = _consumable_line(c) if c.get("sku_class") == "consumable" else _durable_line(c)
    if not c.get("on_list", True):
        line = "Off-list find — " + line
    return line


def _item_dict(c):
    sku_cfg = catalog.CATALOG.get(c.get("sku")) or {}
    d = {
        "sku": c.get("sku"), "name": c.get("name"), "retailer": c.get("retailer"),
        "verdict": c.get("verdict"), "url": c.get("url"),
        "is_repeat": bool(c.get("is_repeat")), "headline": _headline(c),
    }
    for key, fn in ITEM_BLOCKS:
        d[key] = fn(c)
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
    d = _item_dict(c)
    badge = C.VERDICT_LABEL.get(d["verdict"], d["verdict"] or "")
    color = C.VERDICT_COLOR.get(d["verdict"], "#333")
    if d["is_repeat"]:
        return (f"<div style='padding:4px 0;font-size:13px;color:#999'>"
                f"<b style='color:{color}'>{_esc(badge)}</b> {_esc(d['headline'])} (repeat)</div>")
    lines = [
        f"<div style='padding:10px 0;border-bottom:1px solid #eee'>"
        f"<div style='font-size:12px;font-weight:bold;color:{color}'>{_esc(badge)}</div>"
        f"<div style='font-size:15px;font-weight:bold'>{_esc(d['headline'])}</div>"
    ]
    for key, _fn in ITEM_BLOCKS:
        if d.get(key):
            lines.append(f"<div style='font-size:13px;color:#555;margin:2px 0'>{_esc(d[key])}</div>")
    if d.get("url"):
        lines.append(f"<div style='font-size:12px'><a href='{_safe_url(d['url'])}'>link</a></div>")
    lines.append("</div>")
    return "".join(lines)


def _render_item_text(c):
    d = _item_dict(c)
    badge = C.VERDICT_LABEL.get(d["verdict"], d["verdict"] or "")
    if d["is_repeat"]:
        return f"[{badge}] {d['headline']} (repeat)"
    lines = [f"[{badge}] {d['headline']}"]
    for key, _fn in ITEM_BLOCKS:
        if d.get(key):
            lines.append(d[key])
    if d.get("url"):
        lines.append(d["url"])
    return "\n".join(lines)


def _reject_footer_lines(rejects):
    lines = []
    for o in rejects:
        reason = o.get("reject_reason")
        name = o.get("name") or o.get("sku") or "?"
        if reason == "over_par" and o.get("sku_class") == "consumable":
            sku_cfg = catalog.CATALOG.get(o.get("sku")) or {}
            par, up = sku_cfg.get("par_eur"), o.get("unit_price_eur")
            unit = o.get("unit") or sku_cfg.get("unit") or "kg"
            if up is not None and par is not None:
                lines.append(f"{name} — over_par: €{up:.2f}/{unit} vs €{par:.2f}/{unit} par")
                continue
        lines.append(f"{name} — {reason}")
    return lines


def par_review_lines(hist):
    """-> list[str]. Skus whose observed REGULAR median sits far from the user's par.

    This is the visible half of `C.effective_par`, and it exists because that function
    deliberately CLAMPS drift to PAR_DRIFT_MAX. A par set 40% away from what the shops
    genuinely charge is clamped to 15% and then quietly used forever: the verdicts stay
    self-consistent, nothing errors, and the user never learns their number is wrong.
    Correcting a par is a human decision (CLAUDE.md), so the pipeline reports the gap
    and changes nothing. During weeks 1-4 this is the highest-value line in the email.

    Reads the regular series ONLY — a gap measured against promo prices would just be
    the discount, and would tell the user to chase their par downhill every week.
    """
    lines = []
    for sku, cfg in sorted(catalog.CATALOG.items()):
        par = cfg.get("par_eur")
        if not isinstance(par, (int, float)) or par <= 0:
            continue  # no hand-set par -> nothing to review against
        reg = (history.stats_for(hist, sku).get("regular") or {})
        median = reg.get("median")
        if (not isinstance(median, (int, float)) or median <= 0
                or (reg.get("n") or 0) < C.REGULAR_MIN_N
                or (reg.get("span_days") or 0) < C.REGULAR_MIN_SPAN_DAYS):
            continue  # not enough non-promo evidence to make a claim
        gap = median / par - 1
        if abs(gap) < C.PAR_REVIEW_MIN_GAP:
            continue
        unit = cfg.get("unit") or "kg"
        direction = "above" if gap > 0 else "below"
        lines.append(
            f"{sku}: your par is €{par:.2f}/{unit}, but the regular price has been "
            f"€{median:.2f}/{unit} (n={reg['n']} over {reg['span_days']}d) — "
            f"{abs(gap) * 100:.0f}% {direction} your par. Consider updating par_eur."
        )
    return lines


def _retailer_sort_key(r):
    try:
        return (0, catalog.RETAILER_ORDER.index(r))
    except ValueError:
        return (1, r or "")


def _grouped_by_retailer(items):
    by_retailer = {}
    for c in items:
        by_retailer.setdefault(c.get("retailer") or "Other", []).append(c)
    for group in by_retailer.values():
        group.sort(key=lambda c: (0 if c.get("verdict") == C.VERDICT_STRONG else 1, -(c.get("rank_score") or 0)))
    return sorted(by_retailer.items(), key=lambda kv: _retailer_sort_key(kv[0]))


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


def build_email_html(subject, top5, emailable, rejects, reports, stale, today, par_reviews=()):
    on_list_items = [c for c in emailable if c.get("on_list", True)]
    offlist_items = [c for c in emailable if not c.get("on_list", True)][:C.MAX_OFFLIST_LINES]

    top5_html = "".join(_render_item_html(c) for c in top5)
    retailer_html = "".join(
        f"<h3>{_esc(retailer)}</h3>" + "".join(_render_item_html(c) for c in items)
        for retailer, items in _grouped_by_retailer(on_list_items)
    )
    offlist_html = "".join(_render_item_html(c) for c in offlist_items)

    reject_lines = _reject_footer_lines(rejects)[:C.MAX_REJECT_LINES]
    reject_html = "".join(f"<div style='font-size:12px;color:#999'>{_esc(l)}</div>" for l in reject_lines)
    if len(rejects) > len(reject_lines):
        reject_html += f"<div style='font-size:12px;color:#999'>... and {len(rejects) - len(reject_lines)} more</div>"

    report_html = "".join(
        f"<div style='font-size:12px;color:{'#c00' if not r.get('ok') else '#999'}'>"
        f"{_esc(r.get('source'))}: {'FAILED' if not r.get('ok') else 'ok'} n={r.get('n')} {_esc(r.get('note') or '')}</div>"
        for r in reports
    )
    health_html = "".join(
        f"<div style='font-size:12px;color:#a15c00'>{_esc(s)}: no match in {C.CATALOG_STALE_RUNS}+ runs</div>"
        for s in stale
    )
    par_html = "".join(
        f"<div style='font-size:12px;color:#a15c00'>{_esc(l)}</div>" for l in par_reviews
    )
    par_block = f"<h3>Par review</h3>{par_html}" if par_html else ""

    return (
        f"<div style='font-family:system-ui,sans-serif;max-width:640px;padding:8px'>"
        f"<h2 style='margin-bottom:4px'>{_esc(subject)}</h2>"
        f"<h3>Top {C.TOP_N_BLOCK}</h3>{top5_html}"
        f"{retailer_html}"
        f"<h3>Off-list finds</h3>{offlist_html}"
        f"<h3>Also seen &amp; rejected ({len(rejects)} total)</h3>{reject_html}"
        f"<h3>Source report</h3>{report_html}"
        f"<h3>Catalog health</h3>{health_html}"
        f"{par_block}"
        f"{_CAVEATS_HTML}"
        f"</div>"
    )


def build_email_text(subject, top5, emailable, rejects, reports, stale, today, par_reviews=()):
    on_list_items = [c for c in emailable if c.get("on_list", True)]
    offlist_items = [c for c in emailable if not c.get("on_list", True)][:C.MAX_OFFLIST_LINES]

    parts = [subject, "", f"Top {C.TOP_N_BLOCK}:"]
    parts += [_render_item_text(c) for c in top5]
    for retailer, items in _grouped_by_retailer(on_list_items):
        parts.append(f"\n{retailer}:")
        parts += [_render_item_text(c) for c in items]
    parts.append("\nOff-list finds:")
    parts += [_render_item_text(c) for c in offlist_items]

    reject_lines = _reject_footer_lines(rejects)[:C.MAX_REJECT_LINES]
    parts.append(f"\nAlso seen & rejected ({len(rejects)} total):")
    parts += reject_lines
    if len(rejects) > len(reject_lines):
        parts.append(f"... and {len(rejects) - len(reject_lines)} more")

    parts.append("\nSource report:")
    for r in reports:
        parts.append(f"{r.get('source')}: {'FAILED' if not r.get('ok') else 'ok'} n={r.get('n')} {r.get('note') or ''}")

    parts.append("\nCatalog health:")
    for s in stale:
        parts.append(f"{s}: no match in {C.CATALOG_STALE_RUNS}+ runs")

    if par_reviews:
        parts.append("\nPar review:")
        parts += list(par_reviews)

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
    par_stats = {sku: history.stats_for(hist, sku) for sku in catalog.CATALOG}
    stage2_candidates, stage2_rejects, stage2_stats = prefilter.prefilter(offers, today, par_stats)
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
        discover_offers, today, par_stats)
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
    n_strong = sum(1 for c in audited_candidates if c["verdict"] == C.VERDICT_STRONG)
    n_fair = sum(1 for c in audited_candidates if c["verdict"] == C.VERDICT_FAIR)
    n_skip = len(audited_candidates) - n_strong - n_fair
    print(f"  {n_strong} Strong Buy · {n_fair} Fair · {n_skip} Skip")

    _section("STAGE 7 · DIGEST + STATE")
    seen_state = prune_seen(load_seen())
    for c in audited_candidates:
        c["is_repeat"] = _is_repeat(seen_state, c)
        c["rank_score"] = C.rank_score(c.get("discount"), c.get("saving_eur"), c.get("verdict"), c["is_repeat"])

    emailable = [c for c in audited_candidates if c.get("verdict") in (C.VERDICT_STRONG, C.VERDICT_FAIR)]

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
    par_reviews = par_review_lines(hist)
    if par_reviews:
        print(f"  {len(par_reviews)} par(s) look wrong — see the digest's Par review block")

    sent_items = []
    if n_strong + n_fair >= C.MIN_ITEMS_TO_EMAIL:
        subject = f"Weekly Shopping Hunt — {n_strong} Strong Buy · {n_fair} Fair · {today}"
        top5 = _top5(emailable)
        html_body = build_email_html(subject, top5, emailable, stage2_rejects + discover_rejects,
                                      reports, stale, today, par_reviews)
        text_body = build_email_text(subject, top5, emailable, stage2_rejects + discover_rejects,
                                      reports, stale, today, par_reviews)
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
