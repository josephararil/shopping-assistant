"""history.py — the price memory that makes the free-tier substitute for Keepa work.

state/price_history.json structure (CONTRACT.md section 5):
  skus: {"<sku>": {unit, class,
                    promo:   [{d, retailer, source, price_eur, qty, unit_price_eur, name}],
                    regular: [{d, source, unit_price_eur, note}],
                    stats:   {promo: {n, min, p10, median, last},
                              regular: {n, median, span_days}}}}

Every source feeding this pipeline is a PROMOTIONS feed — every price it observes is a
promo price by construction. `promo` and `regular` are kept as two separate series for
exactly that reason:
  - `promo`   <- EVERY sku-matched offer, KEPT OR REJECTED. Rejects carry the MOST
    information about what a *normal* promo looks like (a store that never records the
    expensive weeks makes a cheap week look like the floor). Feeds `promo_floor` (p10)
    and nothing else.
  - `regular` <- ONLY genuine non-promo evidence (Stage-5 comparator listings). Never a
    leaflet price, never a quarantined lead. Only `regular` may inform a par. A par
    blended from promo prices would walk downhill every week until the digest went
    silently empty — the most likely, most invisible way this design fails.

Also owns two smaller stores that ride along with the same load/save/prune shape:
  - the ledger (state/ledger.json): one row per evaluated lead per run, including
    rejects, for prompt calibration and the failed_gates histogram.
  - catalog health (state/catalog_health.json): how a match rule that silently never
    fires gets found — a sku unmatched for CATALOG_STALE_RUNS runs is a bad rule, not
    a fact about the market.
"""

import datetime as dt
import os

import config as C
import common as X

_HISTORY_FILE = "price_history.json"
_HISTORY_MD   = "history.md"
_LEDGER_FILE  = "ledger.json"
_HEALTH_FILE  = "catalog_health.json"


def _clip(text, limit):
    """Clip text at the last word boundary before limit, appending an ellipsis."""
    if not text or len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def _promo_stats(promo_obs):
    """Recompute promo stats from scratch. Never lazy — called on every write."""
    usable = [o for o in promo_obs if isinstance(o.get("unit_price_eur"), (int, float))]
    if not usable:
        return {"n": 0, "min": None, "p10": None, "median": None, "last": None}
    vals = [o["unit_price_eur"] for o in usable]
    newest = max(usable, key=lambda o: o.get("d", ""))
    return {
        "n": len(vals),
        "min": min(vals),
        # C.percentile, not a homegrown p10 — two implementations would silently disagree.
        "p10": C.percentile(vals, C.PROMO_FLOOR_PERCENTILE),
        "median": C.percentile(vals, 0.5),
        "last": newest["unit_price_eur"],
    }


def _regular_stats(regular_obs):
    """Recompute regular stats from scratch. Never lazy — called on every write."""
    usable = [o for o in regular_obs if isinstance(o.get("unit_price_eur"), (int, float))]
    if not usable:
        return {"n": 0, "median": None, "span_days": 0}
    vals = [o["unit_price_eur"] for o in usable]
    dates = sorted(o.get("d") for o in usable if o.get("d"))
    span_days = 0
    if len(dates) >= 2:
        span_days = (dt.date.fromisoformat(dates[-1]) - dt.date.fromisoformat(dates[0])).days
    return {"n": len(vals), "median": C.percentile(vals, 0.5), "span_days": span_days}


def _sku_entry(hist, sku):
    hist.setdefault("skus", {})
    return hist["skus"].setdefault(sku, {
        "unit": None, "class": None,
        "promo": [], "regular": [],
        "stats": {"promo": _promo_stats([]), "regular": _regular_stats([])},
    })


# ── load / save ──────────────────────────────────────────────────────────────

def load():
    """Load state/price_history.json. Returns a fresh {"skus": {}} on missing/corrupt file."""
    hist = X.load_json(_HISTORY_FILE, {"skus": {}})
    hist.setdefault("skus", {})
    return hist


def save(hist):
    """Write state/price_history.json and the state/history.md digest."""
    X.save_json(_HISTORY_FILE, hist)
    _write_md(hist)


# ── write ────────────────────────────────────────────────────────────────────

def record_promo(hist, cand):
    """Append a promo observation for a sku-matched offer, kept OR rejected.

    Refuses (returns False, prints a warning) when the candidate is quarantined.
    `quarantine` means "we don't trust our own arithmetic" — distinct from `skip`
    ("evaluated, not worth it"). A quarantined unit price in the promo series sets a
    phantom floor (via promo_floor's p10) and silences that product permanently."""
    if cand.get("quarantine"):
        print(f"history.record_promo: refused quarantined candidate "
              f"(sku={cand.get('sku')!r}) — quarantine means we don't trust our own arithmetic")
        return False
    sku = cand.get("sku")
    if not sku:
        print("history.record_promo: refused candidate with no sku")
        return False

    entry = _sku_entry(hist, sku)
    if cand.get("unit"):
        entry["unit"] = cand.get("unit")
    if cand.get("sku_class"):
        entry["class"] = cand.get("sku_class")

    # INVARIANT: every sku-matched offer lands here, kept or rejected. Rejects carry the
    # most information about what a *normal* promo looks like — a store that never
    # records the €15/kg weeks will think €12/kg is expensive.
    entry["promo"].append({
        "d": X.today_iso(),
        "retailer": cand.get("retailer"),
        "source": cand.get("source"),
        "price_eur": cand.get("price_eur"),
        "qty": cand.get("qty"),
        "unit_price_eur": cand.get("unit_price_eur"),
        "name": cand.get("name"),
    })
    entry["stats"]["promo"] = _promo_stats(entry["promo"])
    return True


def record_regular(hist, sku, unit_price_eur, source, note=""):
    """Append a genuine non-promo (Stage-5 comparator) price observation.

    Refuses (returns False, prints a warning) a leaflet ("broshura") source. Every
    source feeding this pipeline is a promotions feed; letting a leaflet price into
    `regular` would blend a promo into the one series that is allowed to move a par."""
    if source == "broshura":
        print(f"history.record_regular: refused broshura source for sku={sku!r} "
              f"— leaflet prices are promos, never regular evidence")
        return False

    entry = _sku_entry(hist, sku)
    entry["regular"].append({
        "d": X.today_iso(),
        "source": source,
        "unit_price_eur": unit_price_eur,
        "note": note,
    })
    entry["stats"]["regular"] = _regular_stats(entry["regular"])
    return True


def prune(hist):
    """Keep the newest C.MAX_OBS_PER_SKU observations per series, drop anything older
    than C.HISTORY_MAX_DAYS (C.DISC_SKU_MAX_DAYS for provisional `disc.*` skus, so
    name-drift junk cannot accumulate a phantom history)."""
    today = dt.date.today()
    for sku, entry in hist.get("skus", {}).items():
        max_days = C.DISC_SKU_MAX_DAYS if sku.startswith("disc.") else C.HISTORY_MAX_DAYS
        cutoff = (today - dt.timedelta(days=max_days)).isoformat()
        for series in ("promo", "regular"):
            obs = [o for o in entry.get(series, []) if o.get("d", "") >= cutoff]
            obs.sort(key=lambda o: o.get("d", ""))
            if len(obs) > C.MAX_OBS_PER_SKU:
                obs = obs[-C.MAX_OBS_PER_SKU:]  # newest N, not oldest
            entry[series] = obs
        entry["stats"]["promo"] = _promo_stats(entry["promo"])
        entry["stats"]["regular"] = _regular_stats(entry["regular"])
    return hist


def stats_for(hist, sku):
    """The sku's stats dict, or {} when the sku isn't tracked yet."""
    return (hist.get("skus", {}).get(sku) or {}).get("stats", {}) or {}


# ── prompt summary ───────────────────────────────────────────────────────────

def summarize_for_prompt(hist, skus=None):
    """Compact, bounded text block for injection into prompts.

    skus: optional iterable of sku strings to restrict the price-history section to.
    Also folds in the most recent ledger outcomes (capped at C.MAX_PROMPT_OUTCOMES) so
    the calibration signal in rejects/failed_gates travels with the price history."""
    lines = []

    entries = hist.get("skus", {})
    if skus:
        wanted = set(skus)
        entries = {k: v for k, v in entries.items() if k in wanted}

    def _last_activity(item):
        _, v = item
        ds = [o.get("d", "") for o in (v.get("promo") or []) + (v.get("regular") or [])]
        return max(ds) if ds else ""

    ranked = sorted(entries.items(), key=_last_activity, reverse=True)[:C.MAX_PROMPT_SKUS]
    if ranked:
        lines.append("Known price history (state/price_history.json):")
        for sku, v in ranked:
            pstats = (v.get("stats") or {}).get("promo") or {}
            rstats = (v.get("stats") or {}).get("regular") or {}
            parts = [f"  {sku} ({v.get('unit') or '?'})"]
            if pstats.get("n"):
                parts.append(f"promo n={pstats['n']} min={pstats.get('min')} "
                             f"p10={pstats.get('p10')} median={pstats.get('median')}")
            if rstats.get("n"):
                parts.append(f"regular n={rstats['n']} median={rstats.get('median')} "
                             f"span={rstats.get('span_days')}d")
            lines.append(" — ".join(parts))

    ledger = load_ledger()
    recent = sorted(ledger.get("entries", []), key=lambda e: e.get("d", ""), reverse=True)
    recent = recent[:C.MAX_PROMPT_OUTCOMES]
    if recent:
        if lines:
            lines.append("")
        lines.append("Recent ledger outcomes (calibrate to these):")
        for e in recent:
            parts = [f"  {e.get('sku', '?')}: {e.get('verdict', '?')}"]
            if e.get("discount") is not None:
                parts.append(f"disc={e['discount']}")
            if e.get("reject_reason"):
                parts.append(_clip(e["reject_reason"], 80))
            elif e.get("failed_gates"):
                parts.append(f"failed={e['failed_gates']}")
            lines.append(", ".join(parts))

    return "\n".join(lines) if lines else "(no price history yet)"


# ── human-readable digest ────────────────────────────────────────────────────

def _write_md(hist):
    today = X.today_iso()
    lines = [f"# Shop Hunter Price History — updated {today}", ""]

    skus = hist.get("skus", {})
    lines.append(f"## SKUs tracked ({len(skus)})")
    lines.append("")
    if skus:
        for sku, v in sorted(skus.items()):
            pstats = (v.get("stats") or {}).get("promo") or {}
            rstats = (v.get("stats") or {}).get("regular") or {}
            lines.append(f"### {sku} ({v.get('unit') or '?'}, {v.get('class') or '?'})")
            lines.append(
                f"**Promo:** n={pstats.get('n', 0)} min={pstats.get('min')} "
                f"p10={pstats.get('p10')} median={pstats.get('median')} last={pstats.get('last')}"
            )
            lines.append(
                f"**Regular:** n={rstats.get('n', 0)} median={rstats.get('median')} "
                f"span={rstats.get('span_days', 0)}d"
            )
            lines.append("")
    else:
        lines.append("_No SKUs recorded yet._")
        lines.append("")

    with open(os.path.join(X.STATE_DIR, _HISTORY_MD), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ── ledger: one row per evaluated lead per run, including rejects ───────────

def load_ledger():
    led = X.load_json(_LEDGER_FILE, {"entries": []})
    led.setdefault("entries", [])
    return led


def save_ledger(led):
    X.save_json(_LEDGER_FILE, led)


def record_outcome(led, cand):
    """Append one evaluated-lead row, kept OR rejected. Every field is carried even
    when usually None — this feeds the failed_gates calibration histogram, and a
    dropped field is a silently blind spot in that histogram."""
    led.setdefault("entries", [])
    row = {
        "d": X.today_iso(),
        "sku": cand.get("sku"),
        "retailer": cand.get("retailer"),
        "source": cand.get("source"),
        "name": cand.get("name"),
        "price_eur": cand.get("price_eur"),
        "unit_price_eur": cand.get("unit_price_eur"),
        "par_eur": cand.get("par_eur"),
        "reference_price_eur": cand.get("reference_price_eur"),
        "discount": cand.get("discount"),
        "saving_eur": cand.get("saving_eur"),
        "evidence": cand.get("evidence"),
        "evidence_legs": cand.get("evidence_legs"),
        "verdict": cand.get("verdict"),
        "failed_gates": cand.get("failed_gates"),
        "reject_reason": cand.get("reject_reason"),
        "fit_score": cand.get("fit_score"),
        "rank_score": cand.get("rank_score"),
        "emailed": cand.get("emailed", False),
    }
    led["entries"].append(row)
    return row


def prune_ledger(led):
    """Cap the ledger at C.LEDGER_MAX_ENTRIES / C.LEDGER_MAX_DAYS, keeping the newest."""
    cutoff = (dt.date.today() - dt.timedelta(days=C.LEDGER_MAX_DAYS)).isoformat()
    entries = [e for e in led.get("entries", []) if e.get("d", "") >= cutoff]
    entries.sort(key=lambda e: e.get("d", ""))
    if len(entries) > C.LEDGER_MAX_ENTRIES:
        entries = entries[-C.LEDGER_MAX_ENTRIES:]
    led["entries"] = entries
    return led


# ── catalog health: how a match rule that silently never fires gets found ───

def load_health():
    h = X.load_json(_HEALTH_FILE, {"skus": {}})
    h.setdefault("skus", {})
    return h


def save_health(h):
    X.save_json(_HEALTH_FILE, h)


def bump_health(h, matched_skus, all_skus):
    """Reset runs_since_matched for skus matched this run; increment it for the rest."""
    h.setdefault("skus", {})
    matched = set(matched_skus or ())
    today = X.today_iso()
    for sku in all_skus or ():
        entry = h["skus"].setdefault(sku, {"runs_since_matched": 0, "last_matched": None})
        if sku in matched:
            entry["runs_since_matched"] = 0
            entry["last_matched"] = today
        else:
            entry["runs_since_matched"] = (entry.get("runs_since_matched") or 0) + 1
    return h


def stale_skus(h):
    """Skus unmatched for >= C.CATALOG_STALE_RUNS runs — a bad match rule, surfaced."""
    return [sku for sku, e in (h.get("skus") or {}).items()
            if (e.get("runs_since_matched") or 0) >= C.CATALOG_STALE_RUNS]
