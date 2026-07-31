# Shop Hunter

A weekly Bulgarian shopping-deal digest. It runs itself on free GitHub Actions every Monday at
06:00 UTC, emails only when something is genuinely worth buying, and commits its own price
history back to the repo so that it gets smarter every week.

The problem it solves is not "find discounts" — discounts are everywhere and most of them are
marketing. The problem is telling a **real** promotion from a staged one, without becoming so
strict that the digest is silently empty forever. Every design decision in here serves one side
or the other of that trade-off, and `CLAUDE.md` records which.

Its core split: **the LLM judges, Python does all the arithmetic and all the tiering.** The
model transcribes what it reads (`pack_qty`, `pack_unit`, a reference price, a fit score); Python
divides, converts units, scores evidence, and assigns the verdict. The model cannot tier, cannot
veto a lead, and never divides. A model that reads "100 g" and emits "€1.09/kg" manufactures a
91% discount that clears every gate at once — so it is never asked for a unit price.

## What a run does

```
Stage 0 · HARVEST          deterministic   sources.py    ccc + mydealz RSS + Lidl BG statutory export
Stage 1 · NORMALISE+MATCH  deterministic   match.py      Python does ALL arithmetic
Stage 2 · PREFILTER        deterministic   prefilter.py  -> <=sum(SOURCE_CAPS)  [COST GOVERNOR]
Stage 3 · DISCOVER         LLM #1 (search) the consumable source; Metro + silabg
Stage 4 · AUDIT            LLM #2 (batched, no search)   the procurement audit
Stage 5 · CORROBORATE      LLM #3 (search, gated, <=6)   only leads missing their evidence bar
Stage 6 · VERDICT          deterministic   config.py     Strong Buy / Fair / Skip
Stage 7 · DIGEST + STATE   deterministic   email, price_history, ledger, seen, deals_history
```

Three LLM calls per week, hard-capped candidate counts, no paid APIs, no scraping of individual
product pages. The whole thing fits inside the free tiers.

`config.py` is the frozen contract — every threshold, prompt and schema lives there and nowhere
else. `catalog.py` is your data: **30 consumables**, all non-perishable bulk stock-up items —
frozen meat and fish, tinned and dried staples, hard cheese, coffee, oil, honey, toilet paper.
Perishables the household actually eats fresh (bananas, apples, fresh vegetables) are deliberately
excluded: a bulk buy of something that rots before it's used is not an arbitrage, it's waste, and
this catalog only tracks products a stock-up genuinely extends. Consumables no longer carry a
hand-set target price — their reference is *observed*, computed by `config.reference_for()` from
Lidl Bulgaria's statutory price-transparency export: the same product's own shelf price first,
then the category's p25 shelf price, then (low confidence only) the audit's own reference. The
one exception is `target_eur`, an absolute promote-only pre-commitment, currently set on a single
sku (`supp.whey_protein`, at EUR 25.00/kg). Editing the catalog is still the main way you steer
this thing.

## Setup

Fork or clone, then add these under **Settings → Secrets and variables → Actions**:

| Kind | Name | Notes |
|---|---|---|
| Secret | `ANTHROPIC_API_KEY` | needed when `LLM_PROVIDER` is `anthropic` (the default) |
| Secret | `GEMINI_API_KEY` | needed when `LLM_PROVIDER` is `gemini` |
| Secret | `SMTP_HOST` `SMTP_PORT` `SMTP_USER` `SMTP_PASS` | any SMTP relay; Gmail app passwords work |
| Secret | `EMAIL_TO` `EMAIL_FROM` | where the digest goes, and who it is from |
| Variable | `LLM_PROVIDER` | `anthropic` or `gemini`. Unset = `anthropic` |

The workflow needs `contents: write` (already declared) because the commit-back step is
load-bearing rather than housekeeping: `state/price_history.json` is this project's substitute
for a paid price-history API. It is worthless in week 1 and decisive by week 12, and it only
accumulates because CI commits it back after every run.

Then edit `catalog.py`. Consumables need no manual target — their reference price is learned from
the Lidl statutory export — unless you want a hard pre-commitment, which is what `target_eur` is
for. **Catalog slugs are permanent identifiers** — renaming one resets that product's price
history and its alert-suppression window.

Locally: `pip install -r requirements.txt` (that is `requests` + `python-dotenv`, and the list
does not grow — RSS is parsed with stdlib `xml.etree`, HTML with `re`), and put the same
variables in a `.env`.

## Running it

The offline suites. Every one runs without network in under a second, and CI gates the weekly run
on them so that a parser broken by a site layout change fails loudly instead of harvesting
nothing and producing a digest that merely looks like a quiet week:

```bash
python test_match.py && python test_prefilter.py && python test_history.py && python test_verdicts.py && python test_sources.py && python test_llm_fallback.py && python test_stub.py
```

Two different dry runs, for two different questions:

```bash
python find_deals.py --dry-run
```

*"Do the feeds still work?"* — Stages 0–2 against the live web, then exits **before any LLM
call**. Costs nothing. This is what you run when a source might have changed shape.

```bash
SHOP_HUNTER_DRY_RUN=1 python find_deals.py
```

*"What would this week's email have said?"* — every stage runs and all state is written, but no
email is sent and nothing is marked as seen, so you can run it repeatedly. On Windows
PowerShell: `$env:SHOP_HUNTER_DRY_RUN="1"; python find_deals.py`. **This is the tool for weeks
1–4.**

`C.FORCE_INCLUDE` bypasses alert suppression for one run when you are debugging a single item.

The browsable archive of everything ever emailed, fed by `state/deals_history.json`:

```bash
npm run dev --prefix web
```

## Calibration — weeks 1 to 4 are not production

Target output is **2–6 Strong Buys and 8–20 Fairs per week**. You will not hit that in week 1,
and the reason is structural rather than a bug: a consumable's reference price is observed, not
guessed, so a sku is only judged once it has been *seen*. `category_p25` needs `REGULAR_MIN_N`
(4) shelf observations for that sku — one Lidl export supplies that for many of them at once
(measured 2026-07-31 against the old 44-sku catalog: **17 skus on the first run**; not
re-measured against the current 30-sku catalog), but the rest wait for a week in which Lidl
happens to stock them. Until then the audit's own low-confidence reference fills the gap, and
that caps the verdict at Fair.

`BASELINE_WINDOW_DAYS` (120) is not a warm-up period — it is a ceiling on how far back the
reference looks, so the number tracks inflation instead of averaging over a year and a half of
it. `promo_floor` is the slow one: the 10th percentile of everything a product has been promoted
at needs roughly six weeks of observations before it means anything.

So do not tune by feel. **Read the `failed_gates` histogram in `state/last_run.json` first.** It
records which gate each rejected lead died on, and it points at exactly one knob:

| Dominant gate | What it means | What to change |
|---|---|---|
| `discount` | the discount rungs are set above what this market does | lower `CONSUMABLE_STRONG_DISCOUNT` |
| `evidence` | corroboration is under-firing | **raise `MAX_CORROBORATE_PER_RUN`. Do not lower the evidence bar.** But first check how many of those leads *also* carry `stockup_value` — corroboration only fires on leads failing evidence **alone**, so raising the cap will not reach them |
| `stockup_value` | the watchlist holds items whose bulk saving is under `STOCKUP_MIN_SAVING_EUR` | prune them, raise their `bulk_qty` to what the household really buys, or lower the floor if it is genuinely too strict |
| `near_floor` | the market routinely beats the observed reference | nothing to tune — the reference is already what shops charge |
| `fit` | the watchlist holds things the household does not want | prune the catalog |
| `low_confidence_reference` | too many skus mix product grades | split them; the email's Catalog maintenance block names exactly which ones |

The one rule with no exceptions: **lowering `MIN_EVIDENCE_*` is how this becomes a spam email.**
The evidence model scores how much you should trust the *"before"* price, which is precisely
where marketing nonsense lives — a lone unverifiable retailer claim scores 0.2 against a bar of
1.0, and that is the entire answer to `from X€` badges. Tune the discount rungs instead.

## Before you change anything

Read `CLAUDE.md`. It is not a style guide — it is the list of invariants that hold this thing
together, and each one records a decision that a future implementer will otherwise cheerfully
undo. The short version of the most expensive ones:

- Only genuine non-promo evidence builds a reference. Every feed here except the Lidl statutory
  export is a promotions feed, so a reference learned from them would walk downhill every week
  until nothing qualifies and the digest goes silently empty — `own_shelf` and `category_p25`
  come only from the Lidl export's own shelf-price column, never from a promo price.
- Rejected offers are still recorded as promo observations. They carry the most information
  about what a *normal* promo looks like.
- Nothing keys on prose. `sku` is the key everywhere; `name` is display-only.
- No BGN, anywhere. `parse_eur` returns `None` for a `лв.` amount and there is no conversion
  code.
