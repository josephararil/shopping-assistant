# HANDOFF — finish the Shop Hunter build and open the PR

You are picking up a `/build` run that ran out of budget partway. **Delete this file and
`docs_CONTRACT.md` as the last step before opening the PR.**

Repo: `C:\Users\jharari\Documents\GitHub\shopping-assistant`
Branch: `feat/shop-hunter-pipeline` (14 commits, clean tree, nothing pushed yet)
Base for the PR: `main`
Co-author line for every commit: `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`

## BUDGET RULE — read this first

The user is close to their usage limit. **Be economical.** Do the small things inline
yourself. Spawn at most TWO subagents (the two build tasks in §3), both Sonnet, both with
the full briefs already written below — do not re-derive them, do not re-explore the repo,
do not re-verify any data source. Everything hard-won is recorded here and in `CLAUDE.md`.

## 1. What this project is

A weekly Bulgarian shopping-deal digest. Runs on free GitHub Actions Mondays 06:00 UTC,
emails only when something is genuinely worth buying, commits its own state back. Forked
in spirit from a travel deal-finder (`C:\Users\jharari\Documents\GitHub\deal-hunter`).

Its core design split: **the LLM judges, Python does all arithmetic and all tiering.** The
LLM cannot tier, cannot veto, and never divides.

**Read `CLAUDE.md` in the repo root first — it is complete and authoritative on every
invariant.** Then `config.py` (the frozen contract: field names, units, thresholds, three
prompts, three schemas). `docs_CONTRACT.md` is the original contract note, mostly
superseded by `CLAUDE.md`.

## 2. What is DONE (committed, 216 offline checks green)

| File | State |
|---|---|
| `config.py` (862 ln) | All knobs, evidence model, both verdict fns, 3 prompts, 3 schemas |
| `catalog.py` (818 ln) | 44 consumables + 18 durables = 62 skus, validated |
| `match.py` (346 ln) | fold/tokens/slug/to_base + parse_qty/parse_eur/parse_valid_until/match_sku/annotate |
| `prefilter.py` (201 ln) | The cost governor, 8 reject reasons, first-rule-wins |
| `history.py` (364 ln) | promo/regular store, ledger, catalog_health |
| `sources.py` (232 ln) | `ccc` + `mydealz` RSS fetchers + `harvest()` |
| `common.py` | LLM/email/state helpers, retargeted off travel |
| `CLAUDE.md` | All invariants incl. 4 found during the build |
| `.github/workflows/weekly.yml` | Mondays, 20 min, gates on the offline suites |
| `web/src/App.jsx`, `index.css` | Ported to verdicts, verified via text tools |
| `fixtures/ccc_top_drops.xml`, `mydealz_hot.xml` | Real captures |
| 5 test files | `test_match` 46 · `test_prefilter` 13 · `test_history` 33 · `test_verdicts` 58 · `test_sources` 44 |

Verify with:
```bash
python test_match.py && python test_prefilter.py && python test_history.py && python test_verdicts.py && python test_sources.py
```

## 3. What is LEFT — two build tasks

### TASK A — `find_deals.py` + `test_stub.py` (the driver; nothing exists yet)

Spawn ONE Sonnet agent. Brief it with everything below, verbatim, plus: read
`CLAUDE.md`, `config.py` in full, `deal-hunter/find_city_anomalies.py` (the structure to
port, esp. `main()` at line 581+ and the email builders), `deal-hunter/test_stub.py` (copy
the harness exactly), and the public signatures of `sources`/`prefilter`/`match`/`history`.
Tell it NOT to explore anything else.

**Eight stages in `main()`**, with `_section()` banners:
- **0 HARVEST** `sources.harvest()`. **⚠ CHECK ITS ACTUAL SIGNATURE FIRST** — see §4.
- **1 NORMALISE+MATCH** already done inside `harvest()`; print match rate + `pending_qty` count.
- **2 PREFILTER** `prefilter.prefilter(offers, today)` → `(candidates, rejects, stats)`; print `rejects_by_reason`.
- **3 DISCOVER** (LLM, search) `C.DISCOVER_PROMPT` slots `{today} {household}=C.HOUSEHOLD_CONTEXT {gap_skus} {always_check} {memory}`. `{gap_skus}` = catalog skus with zero matches, capped `C.MAX_GAP_QUERIES` (40), as `sku — label (target €X/unit)`. `{always_check}` names Metro Bulgaria's leaflet and silabg promotions (say in the text that `/promocii` is 404 and the model must find the live URL). **Validate every returned offer Python-side:** sku must exist in `catalog.CATALOG` else drop silently; `price_eur` positive; `source` forced to `"llm_discover"`; `unit_price_eur` **recomputed via `match.to_base`** — never read the model's arithmetic. Then run survivors through `prefilter` so the cap applies.
- **4 AUDIT** (LLM, batched `C.AUDIT_BATCH_SIZE`, no search) `C.AUDIT_PROMPT` slots `{today} {household} {gates}=C.gates_prompt_text() {memory} {leads}`. Assign run-local integer `lead_id`. Response root is a bare ARRAY; match back by `lead_id` with a `_match_candidate` helper, WARN on no match. Apply `pack_qty`/`pack_unit` for `pending_qty` candidates and compute `unit_price_eur` **in Python**. Take `fit_score`, `reference_price_eur`, `ref_confidence`, `trap_detected`, `quality_flag`, prose. Still no usable `unit_price_eur` → `quarantine = True`.
- **5 CORROBORATE** (LLM, search, gated) only leads clearing discount+saving gates but MISSING their evidence bar, capped `C.MAX_CORROBORATE_PER_RUN`. Slots `{today} {household} {min_listings}=C.CORROBORATE_MIN_LISTINGS {leads}`. `direction=="lowers"` → replace reference and **RE-SCORE, never skip it**. `corroborated` with ≥ `C.CORROBORATE_MIN_LISTINGS` listings → add the `corroborated` leg, and `history.record_regular(..., source="corroborate")`.
- **6 VERDICT** (deterministic). Build legs: `user_par` when the sku has `par_eur`; `retailer_claim` when `was_price_eur`; `ccc_was` when `source=="ccc"` and `was_price_eur`; `mydealz_hot` when `heat >= C.MYDEALZ_HOT_DEGREES`; `regular_median` when the sku's regular stats meet `C.REGULAR_MIN_N` and `C.REGULAR_MIN_SPAN_DAYS`; `corroborated` from Stage 5. **`trap_detected` in (`inflated_was_price`,`recurring_evergreen_promo`) DROPS the `retailer_claim` leg before `C.ref_evidence`.** Then `C.verdict_consumable(unit_price, par, C.promo_floor(stats), fit, evidence)` where `par,_ = C.effective_par(sku_cfg, stats)`, or `C.verdict_durable(price, trigger, ref, evidence, fit, on_list)`. `quality_flag=="junk"` demotes Strong Buy→Skip, **one-way**. Then `C.saving_eur_for` and `C.rank_score`.
- **7 DIGEST + STATE**.

**State, every run including silent ones:** `price_history.json`, `ledger.json`, `seen.json`, `deals_history.json`, `catalog_health.json`, `last_run.json`, `run.md`, `history.md`. `history.record_promo` for EVERY sku-matched offer **kept OR rejected** (never for a quarantined lead). `last_run.json` carries source reports, stage counts, `rejects_by_reason` and a **`failed_gates` histogram** (the calibration instrument). `history.bump_health` + surface `history.stale_skus`. **`deals_history.json` appended ONLY from the exact emailed set.**

**Anti-spam:** `C.seen_key` = `sku|price_bucket`, retailer in the RECORD not the key. TTL is the item's own `restock_days` (fallback `C.DEFAULT_RESTOCK_DAYS`), stored as `ttl_days`. **`prune_seen` prunes PER-RECORD against that record's own `ttl_days`** — one global cutoff would delete a 300-day whey suppression after 30 days. Verdict upgrade re-notifies. `C.PRICE_BREAKTHROUGH` overrides suppression. `C.FORCE_INCLUDE` bypasses it for one run. **Repeats demoted not hidden** (out of Top-5, `-C.RANK_REPEAT_PENALTY`, rendered collapsed). **`mark_seen` only after a successful send.**

**Email, 8 blocks:** header `Weekly Shopping Hunt — {n} Strong Buy · {n} Fair · {week}` · Top-5 (`C.TOP_N_BLOCK`, repeat-free) · one section per retailer ordered by `catalog.RETAILER_ORDER` then best `rank_score`, Strong Buy before Fair · off-list (`C.MAX_OFFLIST_LINES`, badged, hard Fair ceiling) · reject footer (`C.MAX_REJECT_LINES` + a count) · source report **including FAILED sources** · catalog health · caveats. Silence rule: email only when `strong_buy + fair >= C.MIN_ITEMS_TO_EMAIL`.

Consumable line, exact shape: `Salmon fillet — €9.80/kg vs €12.00 par (18% under) · buy 5 kg = €49.00, saves €11.00 · freeze in portions · valid to 27 Aug`
Durable line: `Sony WH-1000XM5 — €179 (normal €349, 49% off, saves €170) · your trigger was €200 · corroborated: 2 current listings`

**Two inherited defects that MUST be fixed (explicit plan requirements):**
1. The travel repo HTML-escapes **nothing** — every LLM string and scraped URL goes raw into markup and `href`. Run all text through `html.escape`, all URLs through `urllib.parse.quote(url, safe=":/?&=#%")`.
2. Its field list is **duplicated 3×** across `build_email_html`/`build_email_text`/`build_history_entries` and has already drifted. Define **ONE ordered list of `(key, label, renderer)` blocks** consumed by all three.

**Flags:** `C.DRY_RUN` (env `SHOP_HUNTER_DRY_RUN=1`) runs every stage, writes state, sends no email, no `mark_seen`. `--dry-run` argv runs Stages 0–2 against the live web and **exits before any LLM call**. Every LLM stage in `try/except` that degrades to empty.

**Pass `C.STAGE_*_SCHEMA` objects BY IDENTITY to `X.llm(response_schema=...)`** — `test_stub.py` dispatches on `is`.

**`test_stub.py`** — copy the travel harness exactly (tempdir + `os.chdir`, seed `state/`, monkeypatch `X.llm` dispatching on `response_schema` identity, monkeypatch `sources.harvest`, capture `X.send_email`, `finally: rmtree`). Add `sys.stdout.reconfigure(encoding="utf-8")` — this box's console is cp1252 and fixtures contain `€`/Cyrillic. Assert: retailer sections in `RETAILER_ORDER`; Top-5 present and repeat-free; all three verdict badges; **the exact string `buy 5 kg = €49.00, saves €11.00`**; reject-footer format with a real €/kg-vs-par number; source report **including a FAILED source**; catalog-health line; **`"washing machine" not in` the Strong Buy section** (feed an off-list washing machine 40% off, fit 20); **Sony XM5 €179 vs €200 trigger reaches Strong Buy with NO reference price and renders no `NaN`/`None`/`€None`**; `price_history.json` gained promo observations for **rejected** matched offers; `len(deals_history["entries"]) ==` emailed count exactly; **HTML escaping** (put `<script>alert(1)</script>` and `Ben & Jerry's` in an audit prose field, assert raw `<script>` absent and `&lt;script&gt;` present); a quarantined lead absent from `price_history.json`; `last_run.json` has a non-empty `failed_gates` histogram.

Restrictions: do not edit any existing module or test. No new dependencies, no pytest. If the spec seems wrong, STOP and report.

### TASK B — Lidl statutory price source

Spawn ONE Sonnet agent. Extend `sources.py` and `test_sources.py`, create
`fixtures/lidl_plovdiv.xlsx`. Nothing else.

**Why it matters:** every other source is a promotions feed, so every price is a promo
price by construction. `Цена` here is a genuine **non-promo shelf price** — the only thing
that can populate the `regular` series, without which `effective_par` and the 1.0-weight
`regular_median` leg stay dead until ~week 12.

**Facts I verified — build against these, do NOT re-derive:**
- `C.LIDL_EXPORT_URLS` (two .xlsx). First list: HTTP 200, 6,760,291 bytes, `application/octet-stream`.
- The download **reset once at a short timeout** — use `C.LIDL_HTTP_TIMEOUT` (180), a browser UA, `stream=True`, chunked reads.
- **.xlsx is a ZIP of XML — parse with stdlib `zipfile` + `xml.etree.ElementTree`. NO openpyxl, NO pandas.**
- **Cells are `t="inlineStr"` with `<is><t>…</t></is>` and `xl/sharedStrings.xml` is EMPTY (`count="0"`).** A shared-strings reader returns every cell blank — this bit me.
- **Columns start at B, and empty trailing cells are OMITTED.** Positional indexing silently shifts and makes category numbers look like prices — this also bit me. **Map cells by the `r` attribute's column letter.**
- Header: `B=Код C=Търговски обект D=Наименование на продукта E=Код на продукта F=Категория G=Цена H=Цена в промоция`. Worksheet `xl/worksheets/sheet1.xml`.
- 102,097 rows, 709 unique product codes, 144 stores. Filtering `Търговски обект` to contain `C.LIDL_STORE_FILTER` ("Пловдив") → ~7,799 rows, 11 stores, 709 products, **26 with a non-empty promo**.
- Prices are **EUR**, plain DOT decimal (`7.15`, `0.76`). Established by cross-check: bread `0.76` and 10 eggs `3.01` are implausible as BGN, and T Market's parallel statutory file labels the same columns `в €`.
- Verified Plovdiv promo rows: `Домати на клонка на кг` 1.78→0.99 · `Железница Кашкавал от краве мляко` 9.71→7.75 · `Немско масло` 2.49→1.45 · `Nashe Selo Краве сирене` 7.15→5.99.

Add `_fetch_bytes(url, timeout)` (network chokepoint), `_parse_xlsx_rows(blob)` (→ list of dicts keyed by COLUMN LETTER), `parse_lidl(blob)` (pure, → `(promo_offers, regular_rows)`), `fetch_lidl()` (never raises, → `(promo_offers, regular_rows, report)`), and wire it into `harvest()`.

- `promo_offers`: one canonical offer per row with a non-empty promo, all 11 contract keys, `source="lidl"`, `retailer="Lidl"`, `price_eur`=promo, `was_price_eur`=`Цена`, `claimed_discount` computed, `valid_until=None` (**this export has no validity date — do not invent one**), `url=""`, `heat=None`, `category_hint=None`. **De-dupe across the 11 stores by product code, keeping the LOWEST promo price.**
- `regular_rows`: one per DISTINCT product (lowest `Цена`): `{"name","product_code","price_eur","category"}`. **NOT offers.** The caller records them with `source="lidl_regular"`.
- **Never mix the two series** — that mixing is exactly what the `regular` allowlist exists to prevent.
- Fetch both URLs and merge; one failing while the other succeeds still yields data with a note. Warn (keeping `ok=True`) when distinct products < `C.MIN_EXPECTED_OFFERS["lidl"]` (300).

**Fixture:** download the real export and write a REDUCED copy with the SAME internal structure (inline strings, columns B–H, omitted empty trailing cells) — Plovdiv rows only, ~400–600 rows, including all 26 promo products. Do not hand-author a differently-shaped synthetic file or the fixture stops testing the quirks above. Use stdlib `zipfile`.

**Tests** (extend `test_sources.py`, same `chk` harness): column-letter mapping survives an omitted trailing cell (guard the exact bug); inline strings read correctly with empty sharedStrings; promo count and distinct-product count as observed; the four verified promo pairs with `claimed_discount` to 2dp; de-dupe keeps the LOWEST promo; all 11 keys present, `source=="lidl"`, `valid_until is None`; **no regular row emitted as an offer and no promo price in `regular_rows`** (assert on a product in both); `_fetch_bytes` raising → `([], [], ok=False)`; one URL failing still yields data; below-threshold sets `ok=True` + warning; `harvest()` still returns ccc+mydealz offers when Lidl fails entirely. No test may touch the network.

## 4. Known seams — YOURS to fix, they will not fix themselves

1. **`harvest()` signature.** Task B changes it to also return Lidl's regular rows; Task A's
   agent is briefed against the current 2-tuple. **Decide the signature yourself before
   spawning** (recommend `(offers, reports, regular_rows)`), state it in BOTH briefs, and
   reconcile after. Run Task B first, or brief A with the final signature.
2. **Wire `lidl_regular` into `find_deals`.** Promo offers flow automatically via
   `harvest()`. The regular rows **do not flow anywhere by themselves** — `find_deals` must
   call `history.record_regular(hist, sku, unit_price, source="lidl_regular")` for every
   Lidl regular row matching a catalog sku (use `match.annotate` on a synthetic dict to get
   the sku). **This fails SILENTLY if forgotten** — the evidence leg just stays absent,
   indistinguishable from a normal thin-history week. Assert it in `test_stub.py`, plus that
   a `lidl` promo row never reaches the regular series.
3. **Orphan-constant sweep.** Re-run this after `find_deals` lands; anything still orphaned
   is either dead or a forgotten wiring:
   ```bash
   python -c "import re,pathlib,config as C; d={n for n in dir(C) if n.isupper()}; u=set(); [u.update(re.findall(r'\bC\.([A-Z_][A-Z0-9_]*)',p.read_text(encoding='utf-8'))) for p in pathlib.Path('.').glob('*.py') if p.name!='config.py']; print(sorted(d-u))"
   ```
4. `sources.py` has a private `_parse_de_amount` that is now redundant (I fixed
   `match.parse_eur` to handle thousands separators). Harmless; remove only if trivial.
5. `history.summarize_for_prompt` calls `load_ledger()` internally — I/O inside a
   summariser. Works, but note it if you touch that file.

## 5. Then Phase C — review, verify, PR

1. **Seam review.** `git diff main...HEAD` read as a senior engineer. Check both sides of
   every shared contract: prop/signature agreement, registry references in both directions
   (dead refs AND orphan entries), assumed fields at their construction site, and orphans
   your changes created.
2. **Tick the checklist** at
   `C:\Users\jharari\AppData\Local\Temp\claude\C--Users-jharari-Documents-GitHub-shopping-assistant\3cc71535-07e5-4795-a151-9c6b98fd24f6\scratchpad\CHECKLIST.md`
   — it lists every actionable plan item including the appendix ones no task owns.
3. **Full suite** — all six files must pass.
4. **Live smoke:** `python find_deals.py --dry-run` (Stages 0–2 against the live web, exits
   before any LLM call). Confirm the fixtures still match reality and the caps give a sane
   candidate count.
5. **Prove new guards bite** — break each load-bearing assertion deliberately, watch it go
   red, revert. Do this at minimum for the HTML-escaping assertion and the
   `lidl_regular`-recorded assertion.
6. **`README.md`** — does not exist yet. Write it: what it does, the free-tier setup
   (secrets `ANTHROPIC_API_KEY`/`GEMINI_API_KEY`/`SMTP_*`/`EMAIL_TO`, var `LLM_PROVIDER`),
   how to run the suites and the two dry-run modes, and how to tune from `failed_gates`.
7. **Delete `HANDOFF.md` and `docs_CONTRACT.md`**, then push and open the PR against `main`.

## 6. What the PR body MUST say

Lead with the problem and the design principle, then evidence with concrete numbers. Then:

**Three things the build DISPROVED about the approved plan — say these plainly, the user
may have already acted on them:**
1. **`broshura.bg` does not work.** The plan called it "✅ Works, product-level, ~1552
   offers". Measured: a plain GET returns 218 KB containing **five** EUR amounts; the XHR
   endpoint, `?page=2` and the category path all return the same SPA shell; and rendered in
   a real browser with JS it shows price-less brochure tiles plus a furniture widget with no
   retailer and no date. Its own Risk #1 ("single point of failure for the whole consumable
   half") was already realised. Stage 3 became the primary consumable source instead.
2. **No consumable could ever have reached Strong Buy.** `verdict_consumable` requires
   `evidence >= 1.0` but a leaflet's only leg is `retailer_claim` at 0.2, and the 1.0 legs
   need weeks of history only corroboration can build. The plan's 2–6/week target was
   unreachable. Fixed with the user-approved `user_par` leg.
3. **The plan's `rank_score` crashes on its own flagship example.** `verdict_durable`
   returns `discount=None` on a trigger hit with no reference — the Sony XM5 case — and the
   plan's `rank_score` raises `TypeError` on it.

**Also report these four defects found and fixed:**
- `parse_eur` understated thousands-separated prices by up to **1000×** (`2.499€` → `2.499`).
  A €2499 TV read as €2.499 is below every `trigger_eur`, and a trigger hit deliberately
  bypasses every other gate, so nothing downstream could catch it.
- The `none` veto was exact-token (leaked the smoked-salmon trap); an agent widened it to
  substring-anywhere, which silently vetoed **9 of 52 real feed titles**
  (`spare`⊂`transparent`, `cat`⊂`speedcat`, `liner`⊂`berliner`). Now a prefix test — correct
  for suffixal Bulgarian inflection, zero spurious hits.
- `history.record_regular` was a **denylist** of one string, so every new source got
  par-moving access by default — and test fixtures were already writing `source="ccc"` into
  `regular`. Now an allowlist.
- `prefilter` capped unknown sources at **zero**, so registering a fetcher without a cap
  would discard the whole feed in silence. Now falls back with a loud warning.

**Not verified** (own heading, do not fold into the summary):
- **Browser screenshots do not work in this environment** ("the pane is not displayed, so
  the page is not compositing frames"). The `web/` UI was verified via DOM/text tools and
  console only — **visual confirmation is a manual step for the user.**
- The weekly CI run needs real secrets; `workflow_dispatch` is user-only.
- Every `par_eur` and `trigger_eur` is a researched starting point, not a validated number.
  Weeks 1–4 are calibration. Least confident: `tech.laptop` (€450), `av.tv` (€280),
  `house.chest_freezer` (€200).

**Follow-up not in this PR:**
- **T Market** publishes a richer statutory file (753 products, promo validity dates) at
  `ftp.cloudcart.com/tmarket_kzp/viewer.php?embed=1&clean=1&download=1` — but that host's
  `robots.txt` is `Disallow: /`, so it was deliberately not used. The user's call.
- `billa.bg`, `fantastico.bg`, `kaufland.bg`, `metro.bg` were not checked for their own
  statutory price files (only their homepages were, and `metro.bg` returns 403 on
  `robots.txt`). Each would be another Lidl-grade source if it exists.
- `silabg.com/promo` returns 200 but is a gift-with-purchase threshold list, not discounts.
  Whey protein still depends on Stage-3 search.
