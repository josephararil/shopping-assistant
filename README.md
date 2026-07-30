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
else. `catalog.py` is your data: 44 consumables with a `par_eur` (what you consider a good price
per kilo or litre) and 18 durables with a `trigger_eur` (the price at which you would actually
buy). Editing the catalog is the main way you steer this thing.

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

Then edit `catalog.py`. Set a `par_eur` on the consumables you actually buy and a `trigger_eur`
on the durables you actually want. **Catalog slugs are permanent identifiers** — renaming one
resets that product's price history and its alert-suppression window.

Locally: `pip install -r requirements.txt` (that is `requests` + `python-dotenv`, and the list
does not grow — RSS is parsed with stdlib `xml.etree`, HTML with `re`), and put the same
variables in a `.env`.

## Running it

The offline suites. All six run without network in under a second, and CI gates the weekly run
on them so that a parser broken by a site layout change fails loudly instead of harvesting
nothing and producing a digest that merely looks like a quiet week:

```bash
python test_match.py && python test_prefilter.py && python test_history.py && python test_verdicts.py && python test_sources.py && python test_stub.py
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
and the reason is structural rather than a bug: your `par_eur` values are guesses until the
market corrects them, and `promo_floor` — the 10th percentile of everything a product has ever
been promoted at — needs roughly six weeks of observations before it means anything.

So do not tune by feel. **Read the `failed_gates` histogram in `state/last_run.json` first.** It
records which gate each rejected lead died on, and it points at exactly one knob:

| Dominant gate | What it means | What to change |
|---|---|---|
| `discount` | the discount rungs are set above what this market does | lower `CONSUMABLE_STRONG_DISCOUNT` |
| `evidence` | corroboration is under-firing | **raise `MAX_CORROBORATE_PER_RUN`. Do not lower the evidence bar.** |
| `abs_savings` | the watchlist is full of low-ticket items | prune the catalog |
| `near_floor` | your pars are above what the market routinely does | lower the pars |
| `fit` | the watchlist holds things the household does not want | prune the catalog |

The one rule with no exceptions: **lowering `MIN_EVIDENCE_*` is how this becomes a spam email.**
The evidence model scores how much you should trust the *"before"* price, which is precisely
where marketing nonsense lives — a lone unverifiable retailer claim scores 0.2 against a bar of
1.0, and that is the entire answer to `from X€` badges. Tune the discount rungs instead.

## Before you change anything

Read `CLAUDE.md`. It is not a style guide — it is the list of invariants that hold this thing
together, and each one records a decision that a future implementer will otherwise cheerfully
undo. The short version of the most expensive ones:

- Only genuine non-promo evidence may move a par. Every feed here is a promotions feed, so a par
  learned from them walks downhill every week until nothing qualifies and the digest goes
  silently empty. That is the most likely way this design fails, and it fails invisibly.
- Rejected offers are still recorded as promo observations. They carry the most information
  about what a *normal* promo looks like.
- Nothing keys on prose. `sku` is the key everywhere; `name` is display-only.
- No BGN, anywhere. `parse_eur` returns `None` for a `лв.` amount and there is no conversion
  code.
- Off-list discoveries can never reach Strong Buy — enforced in code rather than by a threshold,
  because that is *the* spam vector.
