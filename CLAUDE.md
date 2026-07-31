# CLAUDE.md — Shop Hunter

A weekly Bulgarian shopping-deal finder. Runs on free GitHub Actions every Monday 06:00 UTC,
emails only when something is genuinely worth buying, and commits its own state back to the repo.

The user's hard requirement, verbatim, is the design brief:

> It is CRITICAL that this pipeline is ruthless and understands what is a real, true promotion
> vs what is marketing nonsense. Otherwise all the effort will ultimately just create a very
> nice spam email. But also we don't want to be so ruthless and harsh that we don't send
> anything because everything doesn't pass the impossible rules we have given Gemini.

Both halves of that sentence are load-bearing. Most of the invariants below exist to hold one
side or the other, and each records a decision a future implementer will otherwise undo.

## Architecture

```
Stage 0 · HARVEST          deterministic   sources.py    ccc + mydealz RSS
Stage 1 · NORMALISE+MATCH  deterministic   match.py      Python does ALL arithmetic
Stage 2 · PREFILTER        deterministic   prefilter.py  -> <=sum(SOURCE_CAPS)  [COST GOVERNOR]
Stage 3 · DISCOVER         LLM #1 (search) the consumable source; Metro + silabg
Stage 4 · AUDIT            LLM #2 (batched, no search)   the procurement audit
Stage 5 · CORROBORATE      LLM #3 (search, gated, <=6)   only leads missing their evidence bar
Stage 6 · VERDICT          deterministic   config.py     Strong Buy / Fair / Skip
Stage 7 · DIGEST + STATE   deterministic   email, price_history, ledger, seen, deals_history
```

Two ordering rules:
1. **Unit normalisation runs before the audit** — the audit must never see a raw pack price and
   be tempted to reason about "per kilo" itself.
2. **Corroboration runs before the verdict** — evidence arriving after the score is decoration.
   Stage 5 can lower a reference, and must then force a **re-score**.

`config.py` is the frozen contract: every field name, unit, divisor, threshold, prompt and
schema. Knobs live there and nowhere else. `catalog.py` is user-owned data with no logic.

## Invariants

### The LLM never performs arithmetic
It transcribes `pack_qty` / `pack_unit` / `price_value`; **Python** divides and converts. A model
that reads "100 g" and emits "€1.09/kg" manufactures a 91% discount that saturates the ranking,
clears every discount rung and beats every floor — one error amplified by six gates. Never
"simplify" this by asking the model for a unit price. `catalog.UNIT_TO_BASE` is the only place
the g→kg and ml→L divisors exist.

This cuts both ways: **the same class of bug is possible in our own parsers.** `parse_eur` once
read `1.393,28 €` as `393.28` and `2.499 €` as `2.499` — a €2499 television understated 1000×
sits below every `trigger_eur` in the catalog, and a trigger hit deliberately bypasses every
other gate, so nothing downstream could have caught it. Ten regressions in `test_match.py` guard
the separator handling. Treat any money parser as load-bearing.

### Observations split `promo` / `regular`; only `regular` may inform a par
Every source feeding this pipeline is a promotions feed, so every price it observes is a promo
price **by construction**. A par learned from them walks downhill every week until nothing
qualifies and the digest goes silently empty — the most likely way this design fails, and it
fails invisibly.

- `promo` ← every sku-matched offer, **kept OR rejected**. Rejects carry the most information
  about what a *normal promo* looks like: a store that never records the €15/kg weeks will think
  €12/kg is expensive. Feeds `promo_floor()` = p10 of the promo series.
- `regular` ← only genuine non-promo evidence: Stage-5 comparator listings.

`history.record_regular` enforces this with an **allowlist** (`C.REGULAR_ALLOWED_SOURCES`), and it
must stay an allowlist. It began as a denylist of the single string `"broshura"`, which grants
every *new* source par-moving access by default — and that had already bitten: test fixtures were
writing `source="ccc"` into `regular`, treating an Amazon price-drop feed as non-promo evidence.

### `effective_par` clamps to the user's par and never overwrites it
The pipeline may nudge a par by at most `PAR_DRIFT_MAX` (0.15), from the **regular series only**.
A sustained gap surfaces as a par-review line in the email — a human decision, not an automatic
one. Removing the clamp reintroduces silent par erosion.

The clamp is why `find_deals.par_review_lines` must exist and must stay wired: a par set 40% away
from what shops genuinely charge is clamped to 15% and then used **forever**, self-consistently,
with nothing erroring and the user never learning their number is wrong. `PAR_REVIEW_MIN_GAP`
(0.20) is the reporting threshold, and the line reports without changing anything. It reads the
**regular series only** — a gap measured against promo prices is just the discount, and surfacing
it would train the user to chase their par downhill every week, which is failure mode #1 wearing
a helpful face. In weeks 1–4 this is the highest-value line in the digest.

### `ref_evidence` scores REFERENCE credibility, never OFFER credibility
Offer credibility is near-constant across these feeds. **The "before" price is where marketing
nonsense lives.** A lone `retailer_claim` at 0.2 against bars of 1.0 / 2.0 / 2.5 is
mathematically insufficient alone or with any one other weak leg — that is the whole answer to
camelcamelcamel's unverifiable `from X€`.

**Lowering `MIN_EVIDENCE_*` is how this becomes a spam email. Tune the discount rungs instead.**

The `user_par` leg (1.0) is granted only where the sku has a hand-set `par_eur`. It exists because
without it a leaflet consumable's total evidence is 0.2 against a 1.0 bar, so **no consumable
could ever reach Strong Buy** and the whole consumable half of the digest would sit at Fair for
~12 weeks while looking like correct ruthless behaviour. A consumable's reference *is* the user's
own par, which is the most credible reference in the system. Durables have no par and so get no
leg — which is what keeps the bar meaningful for them.

### `trap_detected` is a reported observation, not a veto
Python consumes it by zeroing the `retailer_claim` leg. The LLM cannot kill a lead.

### `quality_flag` is one-way: demote only, never promote
It may turn `Strong Buy` into `Skip`. It is not for "they don't need this" (that is `fit_score`)
and not for "the discount looks fake" (that is `trap_detected`). This is the LLM's only lever over
the outcome, and it is a narrow deliberate exception.

### Off-list discovery can never exceed Fair
Enforced in code via `OFFLIST_FAIR_CEILING`, not by a threshold, because this is *the* spam
vector and a tuning mistake must not be able to open it. Off-list **consumable** discovery is cut
entirely — with no par there is nothing to compute €/kg against.

### `quarantine` ≠ `skip`
`skip` means "evaluated, not worth it". `quarantine` means "we don't trust our own arithmetic".
Quarantined leads never enter `record_promo`: a quarantined unit price in the promo series would
set a phantom floor and silence that product permanently.

### Nothing keys on prose
`name` is display-only. `sku` is the key in `price_history`, `seen`, the ledger, catalog lookups
and email grouping. The travel repo this was forked from substring-matched free-text labels
(`alias.lower() in lookup` matches "Sony" inside "Sony Center Berlin") and silently applied the
wrong par to every Bulgarian deal. **Catalog slugs are permanent identifiers — renaming one
resets that product's history and TTL.**

Matching is asymmetric, deliberately:
- **`any_of` is exact whole-token AND-sets.** `["sony","xm5"]` cannot fire on "Sony TV".
- **`none` is a PREFIX test.** Bulgarian inflection is suffixal, so the stem `"пушен"` vetoes
  `пушена` / `пушено` / `пушени`.

The asymmetry was measured, not guessed, over the 52 real titles in `fixtures/`: exact-token veto
leaks the smoked-vs-fresh trap; substring-anywhere fixes that but silently vetoes 9 genuine deals
(`spare`⊂`transparent`, `cat`⊂`speedcat`, `liner`⊂`berliner`); prefix fixes it with zero spurious
hits. It is justified because the errors are not symmetric — **a missed veto is a false positive**,
costing budget and trust, while a missed match is a miss that `catalog_health` surfaces after
`CATALOG_STALE_RUNS` runs.

### A Strong Buy's TTL is the item's own `restock_days`, not a global constant
A recurring salmon promo the household already stocked up on stays quiet ~90 days; a genuinely
annual whey-protein promo re-alerts at ~300. One global TTL cannot do both, so **`prune_seen` must
prune per-record against that record's own `ttl_days`** — one global cutoff would delete a 300-day
whey suppression after 30 days.

**Retailer lives in the seen *record*, not the seen *key*.** Suppression is about the household's
stock, not the shop — five chains carrying one stocked-up item would otherwise be five alerts.
`PRICE_BREAKTHROUGH` still lets a materially better price through, and a verdict upgrade
(Fair → Strong Buy) re-notifies.

**Repeats are demoted, not hidden.** A weekly digest that silently omits the item you are about to
buy is worse than one that shows it quietly.

### `mark_seen` runs only after a successful send
So an SMTP failure retries next run instead of silently swallowing a week.

### `deals_history.json` is appended only from the exact emailed set
It is the `web/` UI's data source and must reflect what was actually sent.

### A `deals_history.json` entry is a CONTRACT with `web/src/App.jsx`, enforced by a test
The email renders its numbers through `_headline()`, but `web/` re-renders them from the raw
fields, so the entry needs both halves: `ITEM_BLOCKS` (prose) **and** `ITEM_DATA_FIELDS`
(structured numbers). Both are consumed by `_item_dict`; **add a field to a list, never to
`_item_dict` directly.**

This drifted once and failed invisibly: 15 of the 26 fields App.jsx reads were never emitted, so
every card rendered with no price and no score ring, the `sku_class` filter matched nothing, and
`the_math` — a `required` field of `STAGE_AUDIT_SCHEMA` that the prompt teaches with a worked
example — was paid for every run and displayed nowhere. Nothing caught it because `test_stub.py`
asserted the *count* of entries, not their *shape*.

`test_stub.py` now greps `App.jsx` for `e.<field>` and asserts every one is emitted, rather than
comparing against a hand-copied list — a copy being the exact thing that drifted. If you add a
field to App.jsx, that test tells you to emit it.

**`bulk_total_eur` is computed in Python, not in App.jsx.** The stock-up line wants
`unit_price x bulk_qty`; `price_eur` is the PACK price. Passing `price_eur` through rendered
"buy 5 kg = €9.80" instead of €49.00 — wrong in a way that looks entirely plausible, which is why
it has its own assertion. Python owns all arithmetic, in the UI's numbers too.

### Thresholds reach prompts only via `gates_prompt_text()`
The travel repo hardcoded `>= 80` in a prompt while the real gate lived in `STAGE1_MIN_SCORE`; they
drifted and the prompt confidently lied to the model for months.

### A failing source contributes `[]` and a visible report line
It never raises and never silently disappears. `MIN_EXPECTED_OFFERS` warns when a parse
"succeeds" but returns 12 items instead of 1500. Likewise **an unknown source is never capped at
zero** — `SOURCE_CAPS.get(source)` falling back to 0 would discard a whole feed in silence,
indistinguishable from a quiet week; it falls back to `DEFAULT_SOURCE_CAP` with a loud warning.

### No BGN, anywhere
`parse_eur` returns `None` for a `лв.` amount. There is no conversion code and none may be added.

### No new dependencies
`requirements.txt` is `requests` + `python-dotenv`. RSS via stdlib `xml.etree.ElementTree`, HTML
via `re` + `html.unescape`, `.xlsx` via stdlib `zipfile` + a cell scan. Tests are hand-rolled
`chk(name, cond, detail)` + `sys.exit(1)`. **No pytest, no bs4, no feedparser, no openpyxl, no
pandas.** No test touches the network — every parser test reads a committed fixture from
`fixtures/`, including reduced-but-structurally-real copies of both Lidl exports. A reduced
fixture must keep the original's internal quirks (inline strings, empty sharedStrings, omitted
trailing cells, its own real header row); a tidy synthetic file stops testing the thing that
breaks.

## Data sources

| Source | Status |
|---|---|
| `de.camelcamelcamel.com/top_drops/feed` | ✅ RSS works (HTML pages are 403 Cloudflare). 20 items |
| `www.mydealz.de/rss/hot` | ✅ Works. 30 items, `pepper:merchant` price + `106°` heat |
| `lidl.bg` statutory export (2 × .xlsx) | ✅ Works. 709 Plovdiv products, 26 promos, and the **only non-promo shelf price in the system** |
| Stage-3 LLM search | The **primary consumable source** — see below |
| `broshura.bg` | ❌ **Ruled out. Do not re-add.** See below |
| `silabg.com/promocii` | ❌ 404. `/promo` is a gift-with-purchase threshold list, not discounts |
| Metro Bulgaria | ⚠ No feed; covered by Stage 3 |

**The Lidl export is the only source that can populate `regular`.** Everything else here is a
promotions feed, so `Цена` on the statutory export is the single genuine non-promo shelf price
the pipeline ever sees. Without it the 1.0-weight `regular_median` leg and `effective_par` stay
dead until corroboration slowly builds a history. It contributes ~26 regular observations per
run, which brings the leg live in about three weeks rather than twelve.

Parsing it correctly is load-bearing, and the way it breaks is silent:

- **.xlsx is a ZIP of XML.** stdlib `zipfile` only — no openpyxl, no pandas.
- **Cells are `t="inlineStr"` and `xl/sharedStrings.xml` is EMPTY (`count="0"`).** A
  shared-strings reader returns every cell blank.
- **Columns are resolved through the HEADER ROW by exact name, never by position.** The two
  export URLs have *entirely different schemas*: in `ExportSecondList` the first file's letter
  map reads `Категория` (a category **number**) as the regular price and `Код на продукта` as
  the promo price — `{'G':'38','H':'0001229'}` became a €1229 promo, was €38. That fabricated 59
  offers and 59 rows in the `regular` series, and *disabled its own safety net*: the garbage
  inflated the distinct-product count past `MIN_EXPECTED_OFFERS`, so nothing warned. A missing
  column now RAISES rather than falling back to letters — a layout change must surface as a
  degraded source, never as plausible numbers.
- **Header matching is exact, never substring.** `Цена` is a prefix of `Цена в промоция`, so a
  loose match hands back the promo column as the regular price and fabricates a 0% discount on
  every row. The real files survive substring matching only by an accident of capitalisation, so
  the property is pinned on synthetic headers in `test_sources.py`.
- Only the second schema carries `Срок на намаление до`, so only its rows get a `valid_until`.
  The first file has no such column and its rows stay `None` — do not invent one.
- The retailer's own `Процентното изменение` column is **never read**; Python computes
  `claimed_discount`. It is used only as an independent test cross-check that the right two
  price columns were picked rather than merely self-consistent wrong ones.

**Why there is no broshura scraper.** The original plan specified `broshura.bg/oferti` as ~1552
product-level offers carrying name + EUR + BGN + retailer + `Важи до`. That does not reproduce.
Measured 2026-07-30: a plain GET returns 218 KB containing **five** EUR amounts in total, and
`/xhr/popularGridOffers`, `?page=2` and `/hranitelni-stoki` all return the same SPA shell. Rendered
in a real browser with JS executed, the page shows brochure tiles with no prices plus a furniture
widget with no retailer and no validity date; the browser's own network trace exposes no
offer-data endpoint. What the site serves is the scanned-image brochure listing that was already
ruled out as OCR-only. The measurement is recorded at `config.SOURCE_CAPS` so nobody re-adds a
scraper on the strength of the original claim.

Consequence: consumables have no deterministic source. `MAX_GAP_QUERIES` is 40 so Stage 3 covers
the whole watchlist weekly, and `llm_discover`'s cap carries most of the budget.

## Calibration

Target: **2–6 Strong Buys and 8–20 Fairs per week.** Weeks 1–4 are calibration, not production —
the pars are the highest-value and most tedious input and only the user can really supply them,
and `promo_floor` needs ~6 weeks to bite.

**Read the `failed_gates` histogram in `state/last_run.json` before touching any threshold:**

- `discount` dominates → `CONSUMABLE_STRONG_DISCOUNT` is too high
- `evidence` dominates → corroboration is under-firing; **raise `MAX_CORROBORATE_PER_RUN`, do not
  lower the evidence bar**
- `abs_savings` dominates → the watchlist is full of low-ticket items; prune the catalog
- `near_floor` dominates → pars are set above what the market routinely does; lower the pars
- `fit` dominates → the watchlist holds items the household does not actually want

## Development

```bash
python test_match.py && python test_prefilter.py && python test_history.py \
  && python test_verdicts.py && python test_sources.py && python test_stub.py
```

All six run offline in under a second. `weekly.yml` runs them as a separate `tests` job that gates
both the weekly pipeline (`needs: tests`) and every pull request, so a parser broken by a site
layout change fails loudly instead of harvesting nothing and producing a digest that merely looks
like a quiet week. The pipeline job itself is skipped on `pull_request` — it spends LLM tokens,
emails the user and commits state, none of which a PR should do. **Add a new suite to that job's
step list, or it never runs in CI.**

- `python find_deals.py --dry-run` — Stages 0–2 against the live web, exits before any LLM call.
- `SHOP_HUNTER_DRY_RUN=1 python find_deals.py` — every stage, writes state, sends no email and
  does not `mark_seen`. This is the tool for weeks 1–4.
- `C.FORCE_INCLUDE` bypasses suppression for one run, for debugging a single item.
- `npm run dev --prefix web` — the browsable archive, fed by `state/deals_history.json`.

`web/public/data.json` and `web/dist/` are build output: gitignored, regenerated by
`npm run sync-data`. Never commit or hand-edit them.

## Out of scope for v1

So nobody builds them by accident: OCR of scanned brochures · any per-product page fetch (403
risk; the corroborate call covers it) · off-list discovery reaching Strong Buy · off-list
consumable discovery · any BGN handling or conversion · stock or branch-level availability ·
Keepa / Amazon PA-API / any paid API · sparkline charts in `web/` (text stats until the store has
~12 weeks of depth) · a second notification channel.
