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
sat below every `trigger_eur` in the catalog (the now-deleted durable pre-commitment field), and a
trigger hit deliberately bypassed every other gate, so nothing downstream could have caught it.
Ten regressions in `test_match.py` guard the separator handling. Treat any money parser as
load-bearing.

### Observations split `promo` / `regular`; only `regular` may inform the reference
Every source feeding this pipeline is a promotions feed, so every price it observes is a promo
price **by construction**. A reference learned from them walks downhill every week until nothing
qualifies and the digest goes silently empty — the most likely way this design fails, and it
fails invisibly.

- `promo` ← every sku-matched offer, **kept OR rejected**. Rejects carry the most information
  about what a *normal promo* looks like: a store that never records the €15/kg weeks will think
  €12/kg is expensive. Feeds `promo_floor()` = p10 of the promo series.
- `regular` ← only genuine non-promo evidence: Lidl's statutory shelf prices and Stage-5
  comparator listings. This is the series `reference_for`'s L2 reads.

`history.record_regular` enforces this with an **allowlist** (`C.REGULAR_ALLOWED_SOURCES`), and it
must stay an allowlist. It began as a denylist of the single string `"broshura"`, which grants
every *new* source reference-moving access by default — and that had already bitten: test fixtures were
writing `source="ccc"` into `regular`, treating an Amazon price-drop feed as non-promo evidence.

### A `regular` observation carries an identity, and upserts on `(d, product_code)`
Every observation records `retailer`, `product_code` and `name`. Without them the series was
anonymous, and that cost twice. Two runs on one day **double-recorded every shelf price**
(measured 2026-07-31: 50 observations that were an exact doubling of 25), skewing every
percentile and inflating `n` against `REGULAR_MIN_N` so the store looked twice as
well-evidenced as it was. And €1.53 and €3.49 rice could not be told apart from one rice seen
twice.

The key is `(d, product_code)` and **not `d` alone** — one sku legitimately holds several
distinct products on one day; `food.rice` really did have three. A row with no `product_code`
(the Stage-5 corroborate path has none) has no identity to dedupe on and is always appended.

### The two series are capped SEPARATELY
`MAX_OBS_PER_SKU` (40) is the **promo** cap and stays 40: `promo_floor`'s p10 is calibrated to
that sample size, so widening it silently moves every existing floor. `REGULAR_MAX_OBS` (1200)
is the **regular** cap, and it has to be an order of magnitude larger because the series takes
one row per *distinct Lidl product code* per run — measured live against the pre-rewrite
44-sku catalog, `food.coffee_beans` alone contributed **32 rows in a single week**. That sku is
gone (superseded by `food.coffee_ground`, see below), but the sizing argument for `REGULAR_MAX_OBS`
= 1200 still depends on this measurement: it is what a single heavy sku can do to the series, and
nothing about the 30-sku catalog makes a heavy sku less possible. Under one shared cap of 40 the
heavy skus would be
wholly replaced every run, so no rolling window could ever see more than one week of shelf
prices. It would look populated and be blind.

### The reference is OBSERVED, in three levels, and the level is always reported
`par_eur` is gone. All 44 of them — against the original 44-sku catalog, before PR-B cut it to
30 — were guesses from stale model training data, and they were
wrong in **both** directions at once: an €8.00 shampoo par made a €3.79/L Amazon deal a Strong
Buy when Lidl's own shelf shampoo is €2.89, while a €19.00 whey par made €22.50/kg a Skip. Any
static number also decays with inflation. `config.reference_for()` tries three levels in order:

| level | reference | confidence |
|---|---|---|
| L1 `own_shelf` | the **same product code's** own statutory shelf price, from the same export row as the promo price | high |
| L2 `category_p25` | **p25** of that sku's observed Lidl shelf prices within `BASELINE_WINDOW_DAYS` (120) | high, or **low** when `p90/p10 > BASELINE_MAX_SPREAD` (2.0) |
| L3 `llm_reference` | the audit's `reference_price_eur` | **low, always** |
| none | — | Skip. Never invent a denominator. |

**L1 is Lidl-only.** Every source carries a `was_price_eur`, but only Lidl's is a statutory
declaration — Bulgarian law requires it to be the lowest of the preceding 30 days. An Amazon
"was" is a retailer claim worth 0.2–0.3 of an evidence leg, and promoting one to a *reference*
would let a seller set its own denominator: precisely the marketing nonsense this pipeline
exists to reject.

**p25, not mean or median.** It reads as "the cheapest ordinary version of this product at the
cheapest grocer", which is what this household buys, is robust to the long gourmet tail that
wrecks a mean (`food.pasta`, since removed from the catalog for exactly this grade-mixing
problem, reached €47/kg at the boutique end), and is the stricter choice.

**`BASELINE_WINDOW_DAYS` (120) bounds the reference computation only.** `HISTORY_MAX_DAYS` (540)
still keeps the raw observations, so the reference tracks inflation instead of averaging over a
year and a half of it. `history.baseline_stats` computes on READ and never stores: the window is
relative to today, so a stored value goes stale silently.

**`promo_floor` keeps its existing job** — "is this deeper than this product's *usual* promotion?"
— unchanged and orthogonal. A consumable Strong Buy must beat the shelf reference **and** sit near
its own promo floor: cheap versus other shops and cheap versus its own history.

### A low-confidence reference is capped at Fair, in code
Two cases share the ceiling: an L3 `llm_reference` (**no LLM-supplied number is ever the authority
on price**) and a wide-spread L2 whose p25 averages across product grades that are not the same
thing. It is a `low_confidence_reference` entry in `failed_gates`, enforced in code and not by a
threshold, for exactly the reason `OFFLIST_FAIR_CEILING` is: a tuning mistake must not be able to
open a spam vector.

`find_deals.maintenance_lines` is the visible half, and is the direct successor to the deleted
par-review block. The reasoning is the same shape: `verdict_consumable` handles a wide-spread sku
SAFELY, and therefore **silently** — nothing errors while the sku quietly never reaches Strong Buy
again. Measured 2026-07-31 against the pre-rewrite 44-sku catalog, 18 of 27 observed skus were
over the threshold, and `food.rice` mixing €1.53 plain rice with €3.49 (a real measured 2.28x
spread, polluted at the time by a baby-purée row) is a *catalog* problem, not a market fact — the
same class of problem that got `food.pasta` (€1.98 durum against €47.67 boutique, a far wider
spread) removed from the catalog outright in PR-B rather than merely split. The block turns a
wide spread into a finite, shrinking to-do list. It reads the same windowed series the reference
reads, so the line always describes the number the verdicts actually used.

### `target_eur` is PROMOTE-ONLY, and must never be guessed
`target_eur` (consumable, per unit) is a number the user named themselves, so no baseline
inflation can fake it and no other gate applies. It is **never a discount denominator** — a
pre-commitment is a bound on the user's willingness to buy, not a market price.

Because it bypasses every gate, it is one of the most dangerous values in the system. Set it
**only** where the user genuinely holds a number. As of the 2026-07-31 interview exactly one
exists: `supp.whey_protein` at €25.00/kg. Salmon was proposed at €19/kg and deliberately **not**
set — the user buys it at €18/kg *normally*, so a €19 target is always-on, which is a spam vector
rather than a pre-commitment.

### Stock-up value is ranked on, not merely rendered — PR-C
Until PR-C, `saving_eur` (the money a bulk buy actually moves) was computed and printed at the
end of the consumable line, but nothing upstream of the email ever looked at it: a €0.20/kg
20%-off yoghurt and a €5/kg 20%-off chicken breast ranked identically, even though only one of
them is worth a special trip and freezer space. That looked correct — the discount gate and the
fit score both passed — and was exactly the silent gap this pipeline exists to close.

`saving_eur = (reference − unit_price) × bulk_qty` is the money actually moved, and it is now
**both** the Strong Buy floor (`STOCKUP_MIN_SAVING_EUR`, gate name `stockup_value`) and the
`rank_score` ranking weight (`RANK_STOCKUP_WEIGHT`, scaled by `shelf_life_factor`). Because the
same number does both jobs, `bulk_qty` is load-bearing data, not decoration — a wrong `bulk_qty`
both mis-gates and mis-ranks a sku in the same direction.

**`STOCKUP_MIN_SAVING_EUR` (10.00) is Strong-Buy-only.** A lead under the floor still reaches
Fair — demoted, not hidden, the same shape as every other cap in this file. It is the direct
successor to the deleted `DURABLE_MIN_ABS_SAVING_EUR` (40), scaled down for a consumable's
per-restock economics rather than a one-off durable purchase. It is also the #1 knob to revisit
after run 1: read the `stockup_value` count in `state/last_run.json`'s `failed_gates` histogram
before touching it, exactly as with every other gate in this file.

**Invariant SHELF-COVERAGE.** Every `catalog.WATCHLIST` sku must carry a **positive** numeric
`shelf_life_days`. `shelf_life_factor` returns `1.0` — full stock-up credit — for any invalid
value (missing, `None`, non-numeric, zero, negative), on purpose: a missing field must never
silently *demote* a sku, because the demotion would be invisible. The consequence is the
opposite of what it looks like at first read — an omitted field makes a sku rank **higher**, not
lower, and nothing errors. Loudness lives in the test instead, not in the ranking.

**Invariant TARGET-PREFILTER.** `prefilter._reject_reason` must not reject a consumable priced at
or below its `target_eur`. Without this, the rejection is invisible in a specific way: Stage 2
runs *before* any verdict exists, so nothing errors and no gate is recorded in `failed_gates` —
the offer is simply absent from the digest, as if it had never been harvested. The exemption is
bounded by `target_eur` itself, so it is a narrow bypass for a number the user actually holds, not
a loosening of `PREFILTER_REFERENCE_SLACK` for everyone else.

### `ref_evidence` scores REFERENCE credibility, never OFFER credibility
Offer credibility is near-constant across these feeds. **The "before" price is where marketing
nonsense lives.** A lone `retailer_claim` at 0.2 against bars of 1.0 / 2.0 / 2.5 is
mathematically insufficient alone or with any one other weak leg — that is the whole answer to
camelcamelcamel's unverifiable `from X€`.

**Lowering `MIN_EVIDENCE_*` is how this becomes a spam email. Tune the discount rungs instead.**

The `own_shelf` and `statutory_shelf` legs (1.0 each) are granted by the reference LEVEL a
consumable landed on — L1 and L2 respectively. **L3 grants no leg**, because an LLM-supplied
number is not evidence about itself. They replaced a `user_par` leg that was granted for having
a hand-set par at all, and they exist for the same reason it did: without a full-weight leg here
a leaflet consumable's total evidence is 0.2 against a 1.0 bar, so **no consumable could ever
reach Strong Buy** and the whole consumable half of the digest would sit at Fair while looking
like correct ruthless behaviour. The replacement is strictly better evidence: a legally mandated
shelf price rather than a number the user guessed.

### `trap_detected` is a reported observation, not a veto
Python consumes it by zeroing the `retailer_claim` leg. The LLM cannot kill a lead.

### `quality_flag` is one-way: demote only, never promote
It may turn `Strong Buy` into `Skip`. It is not for "they don't need this" (that is `fit_score`)
and not for "the discount looks fake" (that is `trap_detected`). This is the LLM's only lever over
the outcome, and it is a narrow deliberate exception.

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

### `shelf_life_days` is not `restock_days`
PR-B (commit fd89c4a) added `shelf_life_days` to all 30 catalog entries. It is a property of the
**food** — how long it keeps — while `restock_days` is a property of **this household's
consumption** — how long a stock-up lasts them, and the number that drives the anti-spam TTL below.
`food.chicken_breast` is the case that forces the distinction: 60 restock days against 120
shelf-life days. Conflating the two would rank the household's cornerstone freezer item — bought
in bulk, consumed steadily, restocked every two months — below shelf-stable rice that keeps for
years but that nobody eats through in 60 days. They measure different things and neither
substitutes for the other.

**`shelf_life_days`'s consumers arrived in PR-C.** `config.shelf_life_factor` scales the
`rank_score` stock-up term by it, `find_deals.ITEM_DATA_FIELDS` emits it to `deals_history.json`,
and `web/src/App.jsx`'s Drawer renders it as "Keeps ~N days". It was added ahead of anything that
used it specifically so that PR-C never had to read a key that did not exist on 30 catalog
entries it didn't write — see Invariant SHELF-COVERAGE above for what an invalid value does.

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
`the_math` — then a `required` field of `STAGE_AUDIT_SCHEMA` that the prompt taught with a worked
example — was paid for every run and displayed nowhere. Nothing caught it because `test_stub.py`
asserted the *count* of entries, not their *shape*.

`test_stub.py` now greps `App.jsx` for `e.<field>` and asserts every one is emitted, rather than
comparing against a hand-copied list — a copy being the exact thing that drifted. If you add a
field to App.jsx, that test tells you to emit it.

**`bulk_total_eur` is computed in Python, not in App.jsx.** The stock-up line wants
`unit_price x bulk_qty`; `price_eur` is the PACK price. Passing `price_eur` through rendered
"buy 5 kg = €9.80" instead of €49.00 — wrong in a way that looks entirely plausible, which is why
it has its own assertion. Python owns all arithmetic, in the UI's numbers too.

### The audit's prose is exactly two capped notes, not six unbounded fields
`ITEM_BLOCKS` used to hold six fields — `the_math`, `about`, `value_case`, `market_insight`,
`bulk_advice`, `red_flags` — each an unbounded string the audit prompt invited the model to fill
with brand background. Read on a phone, in a scannable card, six paragraphs of prose *was* the
spam vector this pipeline exists to reject, just moved one stage downstream of the discount gates
instead of caught by them. `STAGE_AUDIT_SCHEMA` now carries exactly two: `action_note` (the
recommendation — how many to buy, and the one reason the price justifies it) and `storage_note`
(the logistics — shelf life, freezing, space, packaging), both capped at 15 words in the prompt.
`ITEM_BLOCKS` having exactly two entries, with both renderers iterating it rather than naming a
key, is Invariant TWO-NOTES: hardcoding `d["action_note"]` and `d["storage_note"]` would render
identically today and silently un-cap the count the next time a field is added.

**`LEGACY_WEB_FIELDS` is a shim, not a feature.** `web/src/App.jsx` is out of scope for the digest
rewrite that retired the six fields, and it still reads all six by name — the grep contract above
would fail without them. `find_deals.LEGACY_WEB_FIELDS` emits `the_math`/`about`/`value_case`/
`market_insight`/`bulk_advice`/`red_flags` as `""` (never a fabricated value: App.jsx's `&&` guards
render nothing on an empty string rather than crash on a missing key). **Remove the shim only when
App.jsx migrates to `action_note`/`storage_note`** — filling those keys with `action_note`'s text
in the meantime would make old App.jsx cards look alive while showing the wrong prose in the wrong
slot. *Owed: migrate `web/src/App.jsx` to read `action_note`/`storage_note` and drop
`LEGACY_WEB_FIELDS`.*

### Invariant ONE-PASS: every emailed deal renders exactly once
The digest's duplication was structural, not a data bug: `build_email_html` rendered the Top-N
block, then re-rendered the *full* emailable list again, grouped by retailer — the best deals were
printed twice by construction, and a longer, fuller-looking email was the visible symptom. The fix
partitions the deduped `emailable` list into `tier1` (every Strong Buy, plus Fair up to
`TOP_N_BLOCK`) and `tier2` (a dense one-line-per-deal block for the rest) via `find_deals._tier`,
and `_dedupe_emailable` collapses same-sku/same-name duplicates (keeping the highest `rank_score`)
at the single seam feeding both. `_top5`, `_grouped_by_retailer` and `_retailer_sort_key` are gone.
A dropped duplicate is never passed to `mark_seen` — safe, because the seen key is the sku, not the
retailer (see above), so the surviving twin marks the same key.

### The diagnostics footer is aggregated and capped, not one line per reject
A 70-reject run used to print ~70 near-identical `no_sku_match` lines. `_reject_summary_lines`
groups rejects by reason, most-frequent first, capped at `MAX_REJECT_SUMMARY_LINES` (6) with an
overflow line for the rest — a genuinely informative single reject still gets its own EUR/kg-vs-
shelf line, per the `over_reference` special case. `MAX_MAINTENANCE_LINES` (5) caps the wide-spread
sku warnings the same way, with the stale-sku catalog-health list folded into that same block as
one joined line rather than one line per sku, to hold the digest's own ≤15-line-typical footer
budget. Both replace `MAX_REJECT_LINES`, which is deleted.

### `target_note` is folded into `action_note`, never its own line
The first production digest rendered "beats your €25.00/kg target" as a standalone line below
the card, disconnected from the buying recommendation it actually qualifies. `_metrics()` now
prepends it directly onto `action_note` before either renderer sees it — the two-note card
(Invariant TWO-NOTES above) still gets exactly two bullets, just with the target match folded
into the first one. `target_note` itself is still returned from `_metrics()` for `_headline()`'s
sake (deals_history's legacy headline field); it is simply no longer rendered a second time.

### A Stage-3 discover lead's `sku` is model-assigned, not text-matched — and must still clear the catalog veto
`match_sku()`'s `none` prefix veto (see catalog.py's matching-rules docstring) only ever runs
for deterministic-feed offers, which are matched onto a sku by scanning their name. A
`llm_discover` lead is the opposite shape: Stage 3 hands the model a specific gap sku to search
for (`food.milk — UHT milk (1 L)`), and `_offer_from_discover` accepts whatever `sku` the model
echoes back as the search target — it never runs `match_sku` at all, so the veto that would
catch a mislabeled result never fires. Measured live: the model returned "Прясно мляко" (fresh
milk) tagged `food.milk`, a sku that is UHT-only by design (see PR-B above) specifically because
fresh milk cannot be stocked up on. The label text alone told the model the constraint; nothing
enforced it. `match.violates_veto(name, sku)` reuses the same prefix-veto rule against the
lead's own name and is now called explicitly in the `discover_offers` loop before a lead is kept
— any other sku with a `none` list (smoked salmon/pork, baby purée rice, carrot peas, etc.) gets
the same protection on the discover path that it already had on deterministic feeds.

### A reasoning model that is UNAVAILABLE falls down a tier; a model that REJECTS us does not
`gemini-pro-latest` returned sustained HTTP 503 "experiencing high demand" on 2026-07-31, killing
Stage 3 and stalling Stage 4 — the run produced nothing while each stage ground through its retry
ladder. Backoff cannot fix a capacity outage, so `common._reason_with_fallback` walks
`config.GEMINI_REASONING_FALLBACKS` (pro -> flash). A weaker model that answers beats a better one
that does not.

**Only availability failures fall back** — `_RETRY_STATUSES` plus network errors. A 400 raises
immediately: a malformed request or a bad `responseSchema` fails identically on every model, and
falling back would disguise our own bug as Google's capacity problem. `test_llm_fallback.py` asserts
the 400 case never reaches the fallback model.

**The primary fails over fast.** Grinding the primary and *then* failing over costs the sum of both
ladders. `GEMINI_ATTEMPTS_BEFORE_FALLBACK = 2` applies only while somewhere better exists to go; the
last model in a chain keeps the full ladder.

**`GEMINI_REASONING_TIMEOUT` must stay ahead of the model's honest latency.** Measured 2026-07-31: a
Stage-4 AUDIT call on `gemini-pro-latest` timed out at the old 180 s default and then succeeded in
**179 s** — a one-second margin. A thinking model at a 16k budget takes minutes. If the timeout is
tighter than that, the chain falls to flash on a HEALTHY week and quietly downgrades every
`fit_score`; the fallback exists for a model that is *unavailable*, never for one that is merely
slow. Raise the timeout before touching anything else, and keep `weekly.yml`'s `timeout-minutes`
above `stages x batches x GEMINI_REASONING_TIMEOUT` — Actions minutes are free on a public repo, so
that headroom is free too.

**A fallback is recorded, never silent.** `common.MODEL_FALLBACKS_USED` reaches `last_run.json` as
`model_fallbacks`, because a flash-served AUDIT reasons differently from a pro-served one — a
fallback week's `fit_score`s are not strictly comparable, and that must be explainable rather than
mistaken for a market shift. Do not make this quieter.

The lite search tier (`GEMINI_SEARCH_MODEL`) is never a reasoning fallback; `_gemini_search` already
degrades to `""` and lets the reasoner continue knowledge-only.

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

### `ccc` and `mydealz` survive the durable removal, as consumable-only sources
It would look correct to delete both feeds along with the durable class — camelcamelcamel's price
drops skew electronics, and removing the class that consumed most of them looks like it should
take the feed with it. It does not: Amazon.de's Bulk-brand whey-protein price drops are the
household's real protein-powder channel, and `mydealz`'s hot-deals feed covers the same Amazon.de
merchant. Losing either feed would leave `supp.whey_protein` — the one sku holding a `target_eur`
— with no deterministic promo source, pushing it onto the off-list discovery path that this same
removal just cut entirely. Only the durable-only `category_hint` field is gone from both parsers;
the feeds themselves and their parsing logic are untouched.

### PR-B: the catalog went from 44 skus to 30, and each change has a measured reason
The catalog now holds exactly the non-perishable bulk stock-up items this household actually
buys ahead — frozen meat and fish, tinned and dried staples, hard cheese, ground coffee, oil,
honey, toilet paper. Perishables (bananas, apples, fresh vegetables) are deliberately excluded:
they rot before a bulk buy pays off, so tracking them would only ever manufacture noise, never a
real arbitrage.

**A change to the catalog or its thresholds may make the digest quieter, never louder.** This is
the governing principle for the whole refocus, and every item below is an instance of it: when a
number is uncertain, the conservative direction is always fewer alerts, not more.

Three match rules were corrected in this pass, each because a specific observed row proved the
old rule wrong, and each is the kind of rule a future session mistakes for noise and deletes:

- `food.pork_meat` gained a `"пушен"/"smoked"/"gerauchert"` prefix veto. The dearest observed
  shelf row was Пушено свинско филе at €12.27/kg — not the bulk cut this sku tracks — which alone
  drove the sku's spread to 2.09x and Fair-capped it. Mirrors the existing salmon smoked/fresh
  rule.
- `food.rice` lists its Jasmine two-token groups first so they win `match_conf` "high", while the
  bare `["ориз"]` group is retained at "medium" so a plain rice promo still surfaces rather than
  vanishing. It also gained `пюре`/`бебешк`/`био` vetoes: the observed p25 was polluted by a
  baby-purée row ("Био Пюре ... и ориз") that produced the 2.28x spread cited above.
- `food.milk` is UHT-only now. Fresh milk cannot be stocked up on at all, so tracking it as a
  bulk-arbitrage target was always going to be a category error.

Twenty-one skus were deleted and seven added (`food.coffee_ground`, `food.couscous`,
`food.lutenitsa`, `food.cottage_cheese`, `food.sausages`, `food.peas_tinned`,
`food.chickpeas_tinned`). Two of the seven shipped with a veto validated against the 358 real
Lidl product names in `fixtures/`, and both vetoes exist because the unvalidated rule was
measurably wrong:

- `food.sausages` deliberately vetoes `"луканк"`. An earlier draft listed `["луканка"]` in
  `any_of` and matched 8 rows, six of which were dry-cured charcuterie at ~€15–25/kg rather than
  the ~€4/kg kebapche this household buys. `"луканк"` is a prefix veto so it also kills the
  inflected "Луканкова наденица". 8 hits down to 1.
- `food.peas_tinned` vetoes `"морков"/"carrot"` for the same measured reason: without it
  `["грах"]` matched "Грах с моркови" — peas *with* carrots, a different product. 3 hits down
  to 2.

**`food.coffee_ground` supersedes `food.coffee_beans`, and is an add+delete, not a rename.** The
old series measured whole beans, which the user does not buy; keeping the slug would have carried
whole-bean history and TTL onto a ground-coffee product that never earned it. The coffee price
history restarts deliberately. This does not violate the slug-permanence rule above — that rule
protects a single product's identity across renames; it says nothing about retiring one product
and adding a different one that happens to occupy the same shelf.

**`food.flour` stays deleted even though the user bought flour at €0.69/kg this week.** At that
price no bulk buy clears the incoming `STOCKUP_MIN_SAVING_EUR` floor, so tracking it would only
ever produce Fairs — noise, not signal, and precisely the outcome the governing principle above
warns against manufacturing.

`bulk_qty` corrections, each from the user's own numbers and each affecting `saving_eur` — which
is both the Strong Buy floor and the ranking weight, so raising `bulk_qty` makes a sku *easier*
to promote and must be justified by how the household actually buys, never guessed generously:

- `supp.whey_protein`: 50 -> 20, and **held** at 20 deliberately. The user says the amount they'd
  actually buy is effectively unbounded and they may buy more on a genuinely good deal, but a
  bigger `bulk_qty` only ever makes whey easier to promote, never harder — so 20 is the
  conservative starting point. If whey turns out under-alerted after the first run, raising this
  number is the knob, not a threshold.
- `food.frozen_vegetables`: 6 -> 22.5 kg (9 x 2.5 kg bags — this household buys 2.5 kg bags,
  8–10 at a time, limited by freezer space).
- `food.tomatoes_tinned`: 12 -> 4.8 kg (12 x 400 g tins — the old 12 was a tin *count*
  masquerading as kilograms).
- `food.chicken_breast`: unchanged at 6.0 kg.

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
the pipeline ever sees. Without it the `statutory_shelf` leg and the L2 reference stay
dead until corroboration slowly builds a history. Measured live on 2026-07-31 it contributes
**187 regular observations across 25 skus per run** — up from 22 across 13 before `net_qty`,
because 165 of those shelf rows carry no quantity anywhere in their product name — which
brings the leg live in the first run rather than in twelve weeks.

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
- **`Нетно количество` is the second schema's other exclusive column**, and it is what makes
  €/kg deterministic instead of a guess at what a product title means. Resolve it with
  `header_map.get`, **never `_resolve_lidl_col`** — the first file legitimately has no such
  column, and making it required turns a whole export into a failed source.
- **The merge across the two files must carry a losing row's `net_qty` onto the winner.**
  Measured 2026-07-31: the files list the same 700 products under the same codes at
  *identical* prices, so price-only de-duping keeps the first file's row and discards the
  statutory quantity for **every single product** — 0 of 700 rows survived with one. It fails
  invisibly, because each file parses perfectly on its own and no single-file test can see it.
  `test_sources.py` pins it through `fetch_lidl` with both fixtures, not through `parse_lidl`.

### `net_qty` is a MASS, so it is trusted for `kg` and `L` and never for `pc`

`Нетно количество` is the manufacturer's statutory net-quantity declaration and beats any name
parse — it settles `Боб насипен 200-220/100 г` at 1.0 kg (not the 0.1 the calibre grading
implies, a 10× error that rejected the week's best find as `over_reference`) and supplies a quantity
for the 165 live shelf rows whose names carry none at all.

But it is a net **mass or volume**, never a count. Measured on the committed fixture:
`Тоалетна хартия 8бр` declares **0.766** and `Colgate Четка за зъби 3бр` declares **0.042** —
kilograms of product. Applied to a per-piece sku that turns €3.06 for eight rolls into "€4.00
per roll", the same 10× class of error the calibre guard exists to remove, pointing the other
way. **Per-piece skus keep the name-parsing path**, which reads `8бр` correctly.

One accepted imprecision on `L`: edible oils declare mass even when sold by volume, so a 1 L
bottle of sunflower oil reads 0.917 and its €/L comes out ~9% **high**. That is the source's
own labelling, not our arithmetic, and it errs conservatively — it understates a discount
rather than manufacturing one. Water, milk, shampoo and toothpaste all declare volume and are
exact.

The **calibre guard** in `_QTY_RE` (`(?<![\d][-/])`) is still load-bearing despite all of this:
`ccc`, `mydealz` and `llm_discover` names never carry a `net_qty`.

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

### PR-D: the pre-launch calibration interview (2026-08-01) — 28 skus, tilted ranking

Before letting the pipeline run for real, every `bulk_qty` and the `STOCKUP_MIN_SAVING_EUR`
floor were walked against the user's actual buying behaviour, sku by sku, rather than left as
launch-time guesses. Two patterns fell out of that interview and now govern how `bulk_qty` gets
set for any future sku:

**Tinned/jarred goods all share one rule: 10 units is the normal restock, 25+ is the arbitrage
buy.** The user never buys a single tin — always ~10 — and would go to ~25-30 on a real
discount, to bank the price *and* stop worrying about the item for months. This is now uniform
across every tinned/jarred sku: `tomatoes_tinned` (10.0), `peas_tinned` (10.0),
`chickpeas_tinned` (10.0), `sweetcorn_tinned` (8.5), `tuna_tinned` (3.75, smaller tin), and
`lutenitsa` (8.5, jar size varies so an average jar was assumed). A new sku, **`food.beans_tinned`**
(kidney/black beans), was added under the same rule — the user buys these canned, never dried,
and `food.beans_dried` (which nobody actually buys) was deleted rather than repurposed, the same
way `food.flour` was cut in PR-B.

**Freezer meat has a real ceiling the old numbers didn't reflect.** `chicken_breast` and
`pork_meat` both dropped 6.0/8.0 -> **4.0 kg**: the user is explicit that freezer space (already
committed to `frozen_vegetables`) caps a single meat stock-up at 4 kg regardless of discount
depth, no matter how good the deal. `frozen_vegetables` itself dropped 22.5 -> **20.0 kg** (8 x
2.5 kg bags, not 9 — the user's real range is 4-8 bags per trip). `supp.whey_protein` dropped
20 -> **5.0 kg**: the user's real purchase size is price-tiered (5 kg at €22-25/kg, 2.7 kg at
€25-30/kg, only 20-30 kg for an exceptional branded deal under €22/kg) but the pipeline has one
`bulk_qty` per sku with no price- or brand-branching, so 5 kg was chosen as the honest *typical*
case rather than the exceptional one — `target_eur=25` already gates Strong Buy independently of
this number, so the change doesn't weaken that protection.

**`food.lentils` and `food.yoghurt` were deleted, not tuned.** Both were mathematically unable
to clear `STOCKUP_MIN_SAVING_EUR` even at a 94-98% discount, and the user confirmed neither is
worth tracking at any bulk_qty they'd realistically hold (lentils: rarely eaten; yoghurt: no
realistic discount ever gets there). `food.cottage_cheese`'s `bulk_qty` raised 1.5 -> 6.0 instead
of being deleted, since the user does buy more than the old number assumed. **Net: 30 -> 28 skus**
(three deleted, one added).

**A stale-state trap surfaced during this pass, not a new bug filed against it.** The Section-2
floor table (given at the start of the interview as "measured against the committed fixtures")
listed `food.pork_meat`'s shelf p25 as €3.47/kg. The user's own brochure read (~€8/kg for a
kontrafile cut) contradicted it, and checking `state/price_history.json` directly confirmed the
user was right: the `regular` series' surviving `stats` block shows median €7.83/kg, while the
`regular` observation array itself was empty — a leftover inconsistency from pre-rewrite state
(the same stale state Section 4 already flags for overwrite on the next real run). The €3.47
figure was never reproducible from either committed Lidl fixture. Lesson for future calibration:
a number presented as "measured" is a claim about a specific state file at a specific time, and
is only as good as that file — check it against the live source before accepting a floor
decision built on it, exactly as CLAUDE.md's own validate-the-detector guidance says.

**Ranking retilted 40/40 -> 25/55 (discount/stockup).** The user's stated goal — "this is a
stock-up digest, not a discount digest" — was in tension with equal weighting: a 50%-off item
with a small real saving (e.g. a €9.79 olive-oil saving) was outscoring a 25%-off item moving
similar money, purely on the size of the percentage. `RANK_DISCOUNT_WEIGHT` (40->25) and
`RANK_STOCKUP_WEIGHT` (40->55) now bias the Top-5 ordering toward real money moved rather than
headline percentage. This changes ranking only — no verdict gate moved, so nothing that was
Strong Buy or Fair before is now Skip or vice versa. Every pinned `rank_score` number in
`test_verdicts.py` was recomputed against the new weights, not hand-guessed.

**`HOUSEHOLD_CONTEXT` was rewritten** to mention the dedicated freezer/storage room explicitly,
and to add the household's actual eating pattern (vegetables and lean protein, sous-vide or
oven, never fried) — both feed `fit_score` directly and were previously absent, so the audit was
judging fit without knowing either.

Every threshold in `## Verdict gates`, `Evidence bar`, `Reference / baseline`, `Anti-spam`, and
`Cost / volume knobs` was walked and deliberately left unchanged in this pass — see this
section's git history for the reasoning recorded at the time, group by group. `MIN_EVIDENCE_FAIR`
and `MIN_EVIDENCE_STRONG` were explicitly reconfirmed as off-limits per the rule above.

## Calibration

Target: **2–6 Strong Buys and 8–20 Fairs per week.** Weeks 1–4 are calibration, not production —
the catalog's scope is the highest-value and most tedious input and only the user can supply it,
and `promo_floor` needs ~6 weeks to bite.

**Read the `failed_gates` histogram in `state/last_run.json` before touching any threshold:**

- `discount` dominates → `CONSUMABLE_STRONG_DISCOUNT` is too high
- `evidence` dominates → corroboration is under-firing; **raise `MAX_CORROBORATE_PER_RUN`, do not
  lower the evidence bar**
- `stockup_value` dominates → the watchlist holds items whose bulk saving is under
  `STOCKUP_MIN_SAVING_EUR`; prune them, raise their `bulk_qty` to what the household really buys,
  or lower the floor. **Check whether these leads also carry `evidence`** — a lead failing both is
  excluded from Stage-5 corroboration by `find_deals`'s exact `failed_gates == ["evidence"]`
  selector, so raising `MAX_CORROBORATE_PER_RUN` will not touch it
- `near_floor` dominates → the market routinely beats the observed reference
- `low_confidence_reference` dominates → too many skus mix product grades; split them. The
  email's Catalog maintenance block names exactly which ones
- `fit` dominates → the watchlist holds items the household does not actually want

## Development

```bash
python test_match.py && python test_prefilter.py && python test_history.py \
  && python test_verdicts.py && python test_sources.py && python test_llm_fallback.py \
  && python test_stub.py
```

They all run offline in under a second. `weekly.yml` runs them as a separate `tests` job that gates
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
risk; the corroborate call covers it) · any durable/wishlist tracking · any BGN handling or
conversion · stock or branch-level availability · Keepa / Amazon PA-API / any paid API ·
sparkline charts in `web/` (text stats until the store has ~12 weeks of depth) · a second
notification channel.
