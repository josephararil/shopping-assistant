# Shop Hunter — FROZEN CONTRACT (authoritative; config.py is its executable copy)

Repo: `C:\Users\jharari\Documents\GitHub\shopping-assistant`  ·  Branch: `feat/shop-hunter-pipeline`
Slug for raw-GitHub URLs: `josephararil/shopping-assistant`

`config.py` and `catalog.py` are ALREADY WRITTEN and COMMITTED. They are the contract.
Never edit them. Never restate a number they hold — import it: `import config as C`.

---

## 1. Units — exactly three spellings, one set of divisors

Base units are `"kg"`, `"L"`, `"pc"` — capital L, lowercase kg/pc. Nothing else, ever.
Raw units from sources are `kg | g | L | ml | cl | pcs`; convert via
`catalog.UNIT_TO_BASE[raw] -> (base, multiplier)`. That dict is the ONLY place the
divisors 0.001 / 0.01 exist. Do not write `/1000` anywhere.

`par_eur` is EUR per the sku's base unit. `unit_price_eur = price_eur / qty` where
`qty` is already in the base unit.

## 2. The offer/candidate dict — one shape, every key always present

Exactly as documented in `config.py`'s module docstring (lines 12–44). Read it.
Unknown values are `None`, never `0`, never `""` (except `url`, which is `""`).

`name` is DISPLAY ONLY. `sku` is the key in price_history, seen, the ledger, catalog
lookups and email grouping. Nothing keys on prose — that bug class is designed out.

## 3. `match.tokens()` — the three-way union, pinned

```
tokens(text) -> set[str], the UNION of:
  A  folded:   accents stripped, Cyrillic->Latin, punctuation -> SPACE, lowercased, split
  B  raw:      lowercased, split on whitespace, edge punctuation stripped
  C  squashed: punctuation REMOVED (not spaced), lowercased, split on whitespace
```

Worked examples that MUST hold (they are why all three views exist):

| input | A | B | C |
|---|---|---|---|
| `"Пушена сьомга"` | `{pushena, syomga}` | `{пушена, сьомга}` | `{пушена, сьомга}` |
| `"Sony WH-1000XM5"` | `{sony, wh, 1000xm5}` | `{sony, wh-1000xm5}` | `{sony, wh1000xm5}` |
| `"6х1.5л"` | `{6h1, 5l}` | `{6х1.5л}` | `{6х15л}` |

So `["sony","wh1000xm5"]` matches via C, `["сьомга"]` via B, `["syomga"]` via A.
`["sony","xm5"]` does NOT match `"Sony WH-1000XM5"` (no bare `xm5` token) — that is
correct and is why `any_of` carries several groups.

A group matches iff it is a SUBSET of the union. `none` vetoes if ANY of its tokens is
in the union. First match in `catalog.CATALOG` order wins.

`match_conf`: `"high"` for a group of >=2 tokens OR any WISHLIST item; `"medium"` for a
single-token group. Medium matches may reach Fair but need corroboration for Strong Buy.

## 4. Arithmetic ownership — the LLM never divides

Python computes every unit price and every discount. The audit TRANSCRIBES `pack_qty` +
`pack_unit` only. If a value in an LLM response looks like a computed unit price,
discard it. `sources`/`match` recompute `unit_price_eur` for `llm_discover` offers too —
never read the LLM's arithmetic.

`pending_qty=True` marks a sku-matched offer whose `parse_qty` failed; the audit
transcribes the pack and Python then divides.

## 5. `promo` vs `regular` — the single most important invariant

`state/price_history.json`:
```
{"skus": {"<sku>": {
    "unit": "kg", "class": "consumable",
    "promo":   [{"d","retailer","source","price_eur","qty","unit_price_eur","name"}],
    "regular": [{"d","source","unit_price_eur","note"}],
    "stats":   {"promo":   {"n","min","p10","median","last"},
                "regular": {"n","median","span_days"}}}}}
```

- `promo` <- EVERY sku-matched offer, KEPT OR REJECTED. Rejects carry the most
  information about what a *normal promo* looks like.
- `regular` <- ONLY genuine non-promo evidence: Stage-5 comparator listings. NEVER a
  leaflet price. NEVER a quarantined lead. CCC's `from W€` only flagged low-trust and
  never alone.
- Only `regular` may inform a par. Every lead here is a promo by construction; a par
  blended from promo prices walks downhill weekly until the digest goes silently empty.
- `stats` recomputed on every write. Use `C.percentile(values, C.PROMO_FLOOR_PERCENTILE)`
  — do not write your own p10, the test asserts against C's.
- Prune: newest `C.MAX_OBS_PER_SKU` per series; `C.HISTORY_MAX_DAYS`; `disc.*` skus at
  `C.DISC_SKU_MAX_DAYS`.
- **`quarantine` != `skip`.** Quarantined leads NEVER enter `record_observation`. A
  quarantined unit price in the promo series sets a phantom floor and silences that
  product permanently.

## 6. Evidence legs — names are fixed

Leg names are exactly the keys of `C.EVIDENCE_WEIGHTS`:
`user_par`, `retailer_claim`, `ccc_was`, `mydealz_hot`, `regular_median`, `corroborated`.
Total via `C.ref_evidence(legs)` where `legs` is an iterable of present leg names.

- `user_par` (1.0) is granted iff the sku has a hand-set `par_eur` — i.e. consumables.
  **This is a deliberate amendment to the plan**, approved by the user: without it no
  consumable could clear `MIN_EVIDENCE_FAIR` from a leaflet and the whole consumable
  half of the digest would sit at Fair for ~12 weeks while looking like correct
  ruthlessness. Durables are unaffected (no par -> no leg).
- `trap_detected` in (`inflated_was_price`, `recurring_evergreen_promo`) -> the CALLER
  drops the `retailer_claim` leg before calling `ref_evidence`. The LLM never vetoes.

## 7. Verdicts — call C, never reimplement

`C.verdict_consumable(unit_price_eur, par_eur, floor, fit_score, evidence)`
`C.verdict_durable(price_eur, trigger_eur, ref_eur, evidence, fit_score, on_list)`
Both return `(verdict, discount, failed_gates)`. `discount` may be `None` (durable
trigger hit with no reference). `C.rank_score` already coerces `None` to 0.

Verdict strings are `C.VERDICT_STRONG` / `_FAIR` / `_SKIP` = `"Strong Buy"` / `"Fair"` /
`"Skip"`. Never a raw literal, never lowercase.

`quality_flag == "junk"` demotes Strong Buy -> Skip. ONE-WAY: it can never promote.

## 8. Generated artifacts — one policy

`web/public/data.json` and `web/dist/` are BUILD OUTPUT, gitignored, regenerated by
`npm run sync-data`. Never commit them, never hand-edit them. `state/*.json` IS
committed (CI commits it back). No agent runs `npm run build`.

## 9. Prohibitions — with the reason, because each records a decision

- **No new dependencies.** `requirements.txt` is `requests` + `python-dotenv`, full stop.
  RSS via stdlib `xml.etree.ElementTree`; HTML via `re` + `html.unescape`. No bs4, no
  feedparser, no pytest. Tests are hand-rolled `chk(name, cond, detail)` + `sys.exit(1)`.
- **No BGN anywhere.** `parse_eur` RETURNS None for a лв./BGN amount. No conversion code
  exists or may be added.
- **Never lower `MIN_EVIDENCE_*`.** That is how this becomes a spam email. Tune the
  discount rungs.
- **Never remove the `effective_par` clamp.** Silent par erosion is failure mode #1.
- **Never ask an LLM for a unit price.** See §4.
- **No network in any test.** Every parser test reads a committed fixture.
- **Thresholds reach prompts only via `C.gates_prompt_text()`.** Never as prose.
- **`mark_seen` runs only after a successful send**, so an SMTP failure retries.
- **`prune_seen` prunes PER-RECORD against that record's own `ttl_days`.** One global
  cutoff would delete a 300-day whey suppression after 30 days.
- **`deals_history.json` is appended only from the exact emailed set.**
- **A failing source contributes `[]` + a visible report line. It never raises.**

## 10. Git discipline for subagents

Branch `feat/shop-hunter-pipeline` is shared. Other agents are editing other files
CONCURRENTLY — **other files WILL show as modified in `git status`; stage only your own
paths.** Commit; never push; never switch branches; never `git add -A`.

Co-author line for every commit:
```
Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
```
