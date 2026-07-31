"""catalog.py — DATA ONLY, no logic. The user's watchlist and vocabulary.

Separated from config.py deliberately: this is hundreds of lines of data that the user
owns and edits, while config.py holds knobs the pipeline owns. The travel repo's
config.py hit 727 lines by mixing knobs, data and prompts, and became unreviewable.

── WHAT THE USER OWNS ───────────────────────────────────────────────────────────
`target_eur` is a USER-OWNED number: an absolute PROMOTE-ONLY pre-commitment —
"yes at this price" — that outranks all discount maths, EUR per unit. Set it ONLY
where the user genuinely holds a number; a guessed pre-commitment bypasses every
other gate and is the most dangerous value in the system.

There is no durable wishlist any more. The user does not buy one-off durables
through this pipeline and already covers Amazon.de with their own
camelcamelcamel.de price alert; the 18 durable entries with guessed `trigger_eur`
values were noise nobody asked for.

There is deliberately NO `par_eur` here any more. 44 hardcoded EUR/kg targets used to
be the reference for every consumable verdict, and all 44 were guesses generated from
stale model training data. They were wrong in both directions at once — an EUR 8.00
shampoo par made a EUR 3.79/L Amazon deal a Strong Buy when Lidl's own shelf shampoo
is EUR 2.89, while a EUR 19.00 whey par made EUR 22.50/kg a Skip. The reference is now
OBSERVED and rolling, from Lidl's statutory price export: see config.reference_for.
Weeks 1-4 are calibration: expect to prune this list after run 1.

── `restock_days` AND `shelf_life_days` ARE DIFFERENT CONCEPTS ──────────────────
Every entry carries both, and they must never be set equal "for convenience":

  restock_days     how long a stock-up LASTS this household — a consumption rate.
                   It is the anti-spam TTL: how long after alerting on this item we
                   stay quiet, because the freezer or cupboard is already full.
  shelf_life_days  how long the PRODUCT keeps — a property of the food, not of the
                   household. It is what makes a stock-up worth doing at all, and it
                   scales the stock-up ranking term.

Chicken breast is the case that forces the distinction: 60 restock days (how fast
6 kg gets eaten) against 120 shelf-life days (how long it stays good frozen).
Conflating the two ranks the user's cornerstone freezer item below shelf-stable rice.
A missing `shelf_life_days` grants FULL stock-up credit rather than none, so it
inflates a rank silently — every sku must carry one, and a test asserts it.

── HOW MATCHING WORKS (and why it cannot fire on prose) ─────────────────────────
An offer matches item X IFF some group in X["match"]["any_of"] is a SUBSET of
match.tokens(offer["name"]), AND no token in X["match"]["none"] appears in that set.
First match in CATALOG ORDER wins, so order is meaningful: put specific skus above
general ones.

`any_of` groups are AND-SETS OF WHOLE TOKENS, never substrings. ["sony","xm5"] cannot
fire on "Sony TV" or on a stray "XM5" alone. The travel repo's `alias.lower() in
lookup` would match "Sony" inside "Sony Center Berlin"; this cannot.

`none` is a HARD VETO and is where accumulated real-world knowledge lives — the
cat-food trap, the phone-case trap, the smoked-vs-fresh trap. EVERY FALSE POSITIVE
EVER SEEN BECOMES ONE TOKEN HERE. That is the maintenance loop.

match.tokens() unions three views of a name, so one rule set serves Bulgarian, German
and English sources:
  - folded:  accents stripped, Cyrillic transliterated, punctuation -> SPACE
             "Пушена сьомга" -> {pushena, syomga};  "WH-1000XM5" -> {wh, 1000xm5}
  - raw:     lowercased, split on whitespace       -> {wh-1000xm5}
  - squashed: punctuation REMOVED, not spaced      -> {wh1000xm5}
Write rules in whichever script is natural; all three hit.

KNOWN LIMITATION, ACCEPTED: Bulgarian inflection (сьомга / сьомгата / сьомгови) are
different tokens and v1 does NOT stem. False positives are far worse than false
negatives here, because a false positive costs budget AND trust. A sku that has never
matched in CATALOG_STALE_RUNS runs is surfaced in the email as a rule to fix.
"""

# ── Consumables: recurring bulk purchases with a target UNIT price ───────────
# unit is exactly one of "kg" | "L" | "pc". target_eur, where present, is EUR per that
# unit — an absolute promote-only pre-commitment, NOT a discount denominator.
# bulk_qty is what "buy and stock up" means for this item — it drives the saving
# figure the user actually reasons with ("buy 5 kg, saves EUR 11").
# restock_days is how long a stock-up lasts, and IS the anti-spam TTL for this item.
# shelf_life_days is how long the product keeps — see the docstring; NOT the same thing.
WATCHLIST = {
    "food.salmon_fillet": {
        "class": "consumable", "label": "Salmon fillet / steak", "unit": "kg",
        "bulk_qty": 5.0, "restock_days": 90, "shelf_life_days": 90,
        "freezable": True,
        "bulk_note": "Portion into 400 g bags, freeze; keeps 3 months.",
        "match": {
            "any_of": [["сьомга"], ["salmon"], ["losos"], ["lachs"]],
            "none": ["пастет", "pate", "котк", "cat", "куче", "dog",
                     "пушен", "smoked", "gerauchert", "консерв", "spread", "храна"],
        },
    },
    "food.chicken_breast": {
        # The case that forces restock_days != shelf_life_days: 6 kg is eaten in ~60
        # days, but it keeps ~120 days frozen. Ranking must use the latter.
        "class": "consumable", "label": "Chicken breast fillet", "unit": "kg",
        "bulk_qty": 6.0, "restock_days": 60, "shelf_life_days": 120,
        "freezable": True,
        "bulk_note": "Freeze in 500 g portions; keeps 4 months.",
        "match": {
            "any_of": [["пилешко", "филе"], ["chicken", "breast"], ["huhnerbrust"],
                       ["пиле", "филе"]],
            "none": ["котк", "cat", "куче", "dog", "храна", "пастет", "pate",
                     "нагетс", "nuggets", "паниран", "breaded", "консерв"],
        },
    },
    "food.olive_oil": {
        "class": "consumable", "label": "Extra virgin olive oil", "unit": "L",
        "bulk_qty": 5.0, "restock_days": 180, "shelf_life_days": 730,
        "freezable": False,
        "bulk_note": "5 L drum. Keeps 2 years unopened; store dark and cool.",
        "match": {
            "any_of": [["маслиново", "масло"], ["olive", "oil"], ["olivenol"],
                       ["зехтин"]],
            "none": ["сапун", "soap", "козметик", "cosmetic", "маслини", "olives",
                     "спрей", "spray", "шампоан", "shampoo"],
        },
    },
    "food.coffee_ground": {
        # Supersedes the deleted food.coffee_beans — a NEW slug, not a rename, so the
        # coffee history restarts deliberately: the old series measured whole beans,
        # which the user does not buy. Ground only, never beans, never capsules.
        "class": "consumable", "label": "Ground coffee", "unit": "kg",
        "bulk_qty": 5.0, "restock_days": 300, "shelf_life_days": 365,
        "freezable": False,
        "bulk_note": "20 x 250 g packs. Sealed ground coffee keeps ~12 months.",
        "match": {
            "any_of": [["кафе", "мляно"], ["мляно", "кафе"], ["ground", "coffee"],
                       ["lavazza"], ["filterkaffee"]],
            "none": ["зърна", "beans", "bohnen", "капсул", "capsule", "dolce",
                     "nespresso", "разтворим", "instant", "3in1", "машина",
                     "machine", "мляко", "milk"],
        },
    },
    "supp.whey_protein": {
        # The named annual-promo case: restock_days ~300 so the yearly silabg promo
        # re-alerts on its own cycle instead of being suppressed by a global TTL.
        # target_eur is the ONE number the user genuinely holds (interview,
        # 2026-07-31): they currently buy Bulk-brand from Amazon.de at ~EUR 30/kg and
        # would buy outright below EUR 25/kg. Promote-only, never a denominator.
        #
        # bulk_qty 20.0 is the user's normal single purchase and is effectively
        # unbounded — they would buy more on a genuinely good deal. It is held at 20
        # ON PURPOSE: bulk_qty drives saving_eur, which is both the Strong Buy floor
        # and the ranking weight, so a bigger number makes whey EASIER to promote.
        # This is the knob to raise if whey turns out to be under-alerted.
        "class": "consumable", "label": "Whey protein powder", "unit": "kg",
        "target_eur": 25.00,
        "bulk_qty": 20.0, "restock_days": 300, "shelf_life_days": 550,
        "freezable": False,
        "bulk_note": "Sealed tubs keep 18-24 months. The yearly promo is the buy window.",
        "match": {
            "any_of": [["whey"], ["протеин", "whey"], ["суроватъчен"], ["wheyprotein"]],
            "none": ["bar", "барче", "шейк", "готов", "ready", "drink", "напитка",
                     "vegan", "веган", "casein", "казеин", "gainer", "гейнър"],
        },
    },
    # ── Bulk food: meat and fish ──────────────────────────────────────────
    "food.pork_meat": {
        "class": "consumable", "label": "Pork (shoulder/leg), bulk cut", "unit": "kg",
        "bulk_qty": 8.0, "restock_days": 75, "shelf_life_days": 120,
        "freezable": True,
        "bulk_note": "Portion and freeze in 500 g bags; keeps 4 months.",
        "match": {
            # The dearest observed shelf row was SMOKED pork (Пушено свинско филе,
            # EUR 12.27/kg), which is not the bulk cut being tracked and drove this
            # sku's spread to 2.09x, Fair-capping it. `none` is a PREFIX test, so the
            # bare stem "пушен" vetoes пушена/пушено/пушени. Mirrors the salmon rule.
            "any_of": [["свинско", "месо"], ["pork"], ["schweinefleisch"],
                       ["свинско", "филе"]],
            "none": ["котк", "cat", "куче", "dog", "храна", "консерв", "пастет",
                     "pate", "салам", "наденица", "sausage", "бекон", "bacon",
                     "пушен", "smoked", "gerauchert"],
        },
    },
    "food.beef_mince": {
        # Widened to all mince (beef / pork / mixed). The SLUG IS UNCHANGED on
        # purpose — slugs are permanent history and TTL keys; only the label and the
        # match rule widen. The old "свин" veto is gone.
        "class": "consumable", "label": "Mince (beef / pork / mixed)", "unit": "kg",
        "bulk_qty": 4.0, "restock_days": 60, "shelf_life_days": 120,
        "freezable": True,
        "bulk_note": "Freeze in 500 g flat portions; keeps 4 months.",
        "match": {
            "any_of": [["телешка", "кайма"], ["свинска", "кайма"], ["смес", "кайма"],
                       ["кайма"], ["beef", "mince"], ["rinderhack"], ["hackfleisch"]],
            "none": ["пиле", "chicken", "кюфте", "meatball", "готово", "ready",
                     "консерв", "canned", "котк", "cat", "куче", "dog"],
        },
    },
    "food.sausages": {
        # Sausages / kebapche — the ~EUR 4/kg bulk grill item, NOT луканка.
        # Validated against 358 real Lidl product names: an earlier draft listed
        # ["луканка"] in any_of and matched 8 rows of which SIX were луканка —
        # dry-cured charcuterie at ~EUR 15-25/kg. That is the same defect the
        # food.pork_meat smoked veto above exists to fix: an expensive cured variant
        # inflating a bulk sku's spread and Fair-capping it. "луканк" is a PREFIX
        # veto so it also kills "Луканкова наденица", which would otherwise slip
        # through on its наденица token. 8 hits -> 1.
        "class": "consumable", "label": "Sausages / kebapche", "unit": "kg",
        "bulk_qty": 4.0, "restock_days": 60, "shelf_life_days": 120,
        "freezable": True,
        "bulk_note": "Freeze in meal portions; keeps 4 months.",
        "match": {
            "any_of": [["кебапче"], ["кебапчета"], ["наденица"], ["наденички"],
                       ["sausages"], ["bratwurst"]],
            "none": ["котк", "cat", "куче", "dog", "храна", "консерв", "вегет",
                     "vegan", "веган", "соев", "soy", "тофу", "tofu",
                     "луканк", "шпек", "салам", "суджук", "пушен"],
        },
    },
    "food.turkey_breast": {
        "class": "consumable", "label": "Turkey breast fillet", "unit": "kg",
        "bulk_qty": 5.0, "restock_days": 60, "shelf_life_days": 120,
        "freezable": True,
        "bulk_note": "Freeze in 500 g portions; keeps 4 months.",
        "match": {
            "any_of": [["пуешко", "филе"], ["turkey", "breast"], ["putenbrust"],
                       ["пуешки", "гърди"]],
            "none": ["пиле", "chicken", "котк", "cat", "куче", "dog", "консерв",
                     "пастет", "pate", "наденица", "sausage"],
        },
    },
    "food.white_fish": {
        "class": "consumable", "label": "White fish fillet (cod/hake), frozen", "unit": "kg",
        "bulk_qty": 4.0, "restock_days": 90, "shelf_life_days": 180,
        "freezable": True,
        "bulk_note": "Already frozen; portion into meal bags on arrival.",
        "match": {
            "any_of": [["бяла", "риба"], ["white", "fish"], ["weissfisch"],
                       ["треска"]],
            "none": ["сьомга", "salmon", "пушен", "smoked", "пастет", "pate",
                     "консерв", "котк", "cat", "куче", "dog", "панир", "breaded"],
        },
    },
    # ── Bulk food: staples ────────────────────────────────────────────────
    "food.rice": {
        "class": "consumable", "label": "Rice (5 kg Jasmine)", "unit": "kg",
        "bulk_qty": 10.0, "restock_days": 180, "shelf_life_days": 540,
        "freezable": False,
        "bulk_note": "Shelf-stable; buy a 10 kg bag and store dry.",
        "match": {
            # The user buys 5 kg Jasmine. The specific Jasmine groups are listed
            # FIRST so they win match_conf "high" (two tokens); the bare ["ориз"]
            # group is retained so a plain rice promo still matches at "medium"
            # rather than vanishing from the digest entirely.
            # The purée vetoes are measured: observed p25 was polluted by a baby-
            # purée row ("Био Пюре ... и ориз"), giving a 2.28x spread.
            "any_of": [["ориз", "жасмин"], ["jasmine", "rice"], ["жасминов", "ориз"],
                       ["ориз"], ["rice"], ["reis"]],
            "none": ["пудинг", "pudding", "десерт", "dessert", "вафли", "cakes",
                     "суши", "sushi", "пюре", "puree", "бебешк", "baby", "био",
                     "каша"],
        },
    },
    "food.couscous": {
        "class": "consumable", "label": "Couscous", "unit": "kg",
        "bulk_qty": 4.0, "restock_days": 180, "shelf_life_days": 540,
        "freezable": False,
        "bulk_note": "Shelf-stable for a year+; store dry and sealed.",
        "match": {
            "any_of": [["кускус"], ["couscous"], ["kuskus"]],
            "none": ["салата", "salad", "готов", "ready", "бебешк", "baby"],
        },
    },
    "food.lentils": {
        "class": "consumable", "label": "Lentils, dried", "unit": "kg",
        "bulk_qty": 5.0, "restock_days": 180, "shelf_life_days": 730,
        "freezable": False,
        "bulk_note": "Shelf-stable for years dry.",
        "match": {
            # "леща" is also Bulgarian for "lens" (glasses/camera) -- hard veto below.
            "any_of": [["леща"], ["lentils"], ["linsen"]],
            "none": ["очила", "glasses", "обектив", "lens", "камера", "camera",
                     "контактни", "contact"],
        },
    },
    "food.beans_dried": {
        "class": "consumable", "label": "Dried beans", "unit": "kg",
        "bulk_qty": 5.0, "restock_days": 180, "shelf_life_days": 730,
        "freezable": False,
        "bulk_note": "Shelf-stable for years dry.",
        "match": {
            # "beans" alone also appears in "coffee beans" -- veto it here so this
            # rule never steals food.coffee_ground's match.
            "any_of": [["боб"], ["beans"], ["bohnen"], ["сух", "боб"]],
            "none": ["кафе", "coffee", "зелен", "green", "консерв", "canned",
                     "tinned"],
        },
    },
    "food.oats": {
        "class": "consumable", "label": "Rolled oats", "unit": "kg",
        "bulk_qty": 6.0, "restock_days": 150, "shelf_life_days": 270,
        "freezable": False,
        "bulk_note": "Shelf-stable for months sealed.",
        "match": {
            "any_of": [["овесени", "ядки"], ["oats"], ["haferflocken"],
                       ["овесена", "каша"]],
            "none": ["бебешко", "baby", "бисквит", "cookies", "бар", "bar",
                     "снек", "snack"],
        },
    },
    # ── Bulk food: tinned goods ───────────────────────────────────────────
    "food.tomatoes_tinned": {
        "class": "consumable", "label": "Tinned tomatoes (chopped/peeled)", "unit": "kg",
        "bulk_qty": 4.8, "restock_days": 150, "shelf_life_days": 730,
        "freezable": False,
        "bulk_note": "12 x 400 g tins. Shelf-stable for 2+ years; stack a case.",
        "match": {
            "any_of": [["домати", "консерва"], ["tinned", "tomatoes"],
                       ["tomaten", "dose"], ["домати", "белени"]],
            "none": ["пресни", "fresh", "пюре", "puree", "кетчуп", "ketchup",
                     "сос", "sauce"],
        },
    },
    "food.tuna_tinned": {
        "class": "consumable", "label": "Tinned tuna", "unit": "kg",
        "bulk_qty": 3.0, "restock_days": 150, "shelf_life_days": 1095,
        "freezable": False,
        "bulk_note": "Shelf-stable for years; stock a case of tins.",
        "match": {
            "any_of": [["риба", "тон"], ["tuna"], ["thunfisch"], ["тон", "консерва"]],
            "none": ["котк", "cat", "куче", "dog", "храна", "пастет", "pate",
                     "прясна", "fresh", "стек", "steak"],
        },
    },
    "food.sweetcorn_tinned": {
        "class": "consumable", "label": "Tinned sweetcorn", "unit": "kg",
        "bulk_qty": 4.0, "restock_days": 150, "shelf_life_days": 1095,
        "freezable": False,
        "bulk_note": "Shelf-stable for years; stock a case of tins.",
        "match": {
            "any_of": [["царевица", "консерва"], ["sweetcorn"], ["mais", "dose"],
                       ["сладка", "царевица"]],
            "none": ["пуканки", "popcorn", "замразена", "frozen", "прясна",
                     "fresh"],
        },
    },
    "food.peas_tinned": {
        "class": "consumable", "label": "Tinned green peas", "unit": "kg",
        "bulk_qty": 4.0, "restock_days": 150, "shelf_life_days": 1095,
        "freezable": False,
        "bulk_note": "Shelf-stable for years; stock a case of tins.",
        "match": {
            # The "морков"/"carrot" veto is measured, not speculative: without it
            # ["грах"] matched "Грах с моркови (ОНТ 530g)" in the fixtures — peas
            # with carrots, a different product at a different price.
            "any_of": [["зелен", "грах"], ["грах"], ["green", "peas"], ["erbsen"]],
            "none": ["супа", "soup", "пюре", "puree", "бебешк", "baby", "снакс",
                     "snack", "чипс", "нахут", "жълт", "морков", "carrot"],
        },
    },
    "food.chickpeas_tinned": {
        "class": "consumable", "label": "Tinned chickpeas", "unit": "kg",
        "bulk_qty": 4.0, "restock_days": 150, "shelf_life_days": 1095,
        "freezable": False,
        "bulk_note": "Shelf-stable for years; stock a case of tins.",
        "match": {
            "any_of": [["нахут"], ["chickpeas"], ["kichererbsen"], ["chick", "peas"]],
            "none": ["хумус", "hummus", "паста", "снакс", "snack", "чипс",
                     "бебешк", "baby"],
        },
    },
    # ── Bulk food: dairy and eggs ─────────────────────────────────────────
    "food.cheese_hard": {
        "class": "consumable", "label": "Hard cheese (cheddar/gouda/parmesan)", "unit": "kg",
        "bulk_qty": 2.0, "restock_days": 60, "shelf_life_days": 60,
        "freezable": True,
        "bulk_note": "Vacuum-wrap and freeze in 250 g blocks; keeps 2 months.",
        "match": {
            "any_of": [["чедър"], ["гауда"], ["едам"], ["пармезан"], ["cheddar"],
                       ["gouda"], ["parmesan"], ["hartkase"]],
            "none": ["крема", "cream", "топено", "processed", "намазка", "spread",
                     "кашкавал"],
        },
    },
    "food.kashkaval": {
        "class": "consumable", "label": "Kashkaval (BG yellow cheese)", "unit": "kg",
        "bulk_qty": 2.0, "restock_days": 45, "shelf_life_days": 60,
        "freezable": True,
        "bulk_note": "Vacuum-wrap and freeze in 250 g blocks; keeps 2 months.",
        "match": {
            "any_of": [["кашкавал"], ["kashkaval"]],
            "none": ["крема", "cream", "топено", "processed", "намазка", "spread",
                     "чедър", "cheddar"],
        },
    },
    "food.cottage_cheese": {
        "class": "consumable", "label": "Cottage cheese (извара)", "unit": "kg",
        "bulk_qty": 1.5, "restock_days": 21, "shelf_life_days": 21,
        "freezable": False,
        "bulk_note": "Lidl own brand, 4-5 tubs a visit. Short life; do not overbuy.",
        "match": {
            "any_of": [["извара"], ["cottage", "cheese"], ["hüttenkäse"],
                       ["huttenkase"], ["крема", "извара"]],
            "none": ["кашкавал", "сирене", "cheddar", "гауда", "десерт", "dessert",
                     "бебешк", "baby"],
        },
    },
    "food.milk": {
        # UHT only. The user buys 1 L UHT to stack and explicitly does not care
        # about fresh milk, which cannot be stocked up on at all.
        "class": "consumable", "label": "UHT milk (1 L)", "unit": "L",
        "bulk_qty": 12.0, "restock_days": 30, "shelf_life_days": 180,
        "freezable": False,
        "bulk_note": "Only UHT stacks; a case keeps ~6 months unopened.",
        "match": {
            "any_of": [["uht", "мляко"], ["uht", "milk"], ["uht"],
                       ["трайно", "мляко"], ["h", "milch"], ["haltbare", "milch"]],
            "none": ["прясно", "fresh", "frischmilch", "кисело", "yoghurt",
                     "йогурт", "сухо", "powder", "бебешко", "baby", "формула",
                     "formula", "шоколадово", "chocolate", "какао", "cocoa",
                     "овесено", "oat", "соево", "soy", "бадемово", "almond"],
        },
    },
    "food.yoghurt": {
        "class": "consumable", "label": "Plain yoghurt", "unit": "kg",
        "bulk_qty": 6.0, "restock_days": 21, "shelf_life_days": 21,
        "freezable": False,
        "bulk_note": "Short shelf life; buy for ~3 weeks at a time only.",
        "match": {
            "any_of": [["кисело", "мляко"], ["yoghurt"], ["joghurt"], ["йогурт"]],
            "none": ["сирене", "cheese", "напитка", "drink", "детско", "baby",
                     "формула", "formula", "бебешко"],
        },
    },
    "food.eggs": {
        # Priced per egg; a 10-pack at ~1.80 EUR is ~0.18 EUR/egg.
        "class": "consumable", "label": "Eggs", "unit": "pc",
        "bulk_qty": 30.0, "restock_days": 30, "shelf_life_days": 35,
        "freezable": False,
        "bulk_note": "Buy ~30 at a time; keep refrigerated 4-5 weeks.",
        "match": {
            "any_of": [["яйца"], ["eggs"], ["eier"]],
            "none": ["шоколадови", "chocolate", "играчка", "toy", "велик",
                     "easter", "прах", "powder", "майонеза", "mayo"],
        },
    },
    # ── Bulk food: condiments and preserves ──────────────────────────────
    "food.honey": {
        # 500 g jar at ~3.50 EUR -> ~7.00 EUR/kg.
        "class": "consumable", "label": "Honey", "unit": "kg",
        "bulk_qty": 3.0, "restock_days": 180, "shelf_life_days": 1095,
        "freezable": False,
        "bulk_note": "Shelf-stable indefinitely; buy several jars.",
        "match": {
            "any_of": [["мед", "пчелен"], ["honey"], ["honig"],
                       ["натурален", "мед"]],
            "none": ["сапун", "soap", "козметик", "cosmetic", "шампоан",
                     "shampoo", "бонбони", "candy", "сладки", "desert"],
        },
    },
    "food.lutenitsa": {
        "class": "consumable", "label": "Lutenitsa", "unit": "kg",
        "bulk_qty": 3.0, "restock_days": 180, "shelf_life_days": 730,
        "freezable": False,
        "bulk_note": "Shelf-stable for 2 years unopened; buy the case at a good promo.",
        "match": {
            "any_of": [["лютеница"], ["lutenitsa"], ["lyutenitsa"]],
            "none": ["люто", "чили", "chili", "сос", "sauce", "кетчуп", "ketchup"],
        },
    },
    # ── Frozen ────────────────────────────────────────────────────────────
    "food.frozen_vegetables": {
        # 9 x 2.5 kg bags — the user buys 2.5 kg bags, 8-10 at a time, limited only
        # by freezer space.
        "class": "consumable", "label": "Frozen mixed/single vegetables", "unit": "kg",
        "bulk_qty": 22.5, "restock_days": 60, "shelf_life_days": 365,
        "freezable": True,
        "bulk_note": "9 x 2.5 kg bags. Already frozen; just needs freezer space.",
        "match": {
            "any_of": [["замразени", "зеленчуци"], ["frozen", "vegetables"],
                       ["tiefkuhlgemuse"], ["замразен", "микс"]],
            "none": ["плодове", "fruit", "пица", "pizza", "готово", "ready",
                     "супа", "soup"],
        },
    },
    # ── Household consumables ────────────────────────────────────────────
    "house.toilet_paper": {
        # 10-roll pack at ~4.50 EUR -> ~0.45 EUR/roll.
        "class": "consumable", "label": "Toilet paper", "unit": "pc",
        "bulk_qty": 40.0, "restock_days": 90, "shelf_life_days": 3650,
        "freezable": False,
        "bulk_note": "Shelf-stable; buy 4 packs at a time.",
        "match": {
            "any_of": [["тоалетна", "хартия"], ["toilet", "paper"],
                       ["toilettenpapier"]],
            "none": ["кухненска", "kitchen", "влажни", "wet", "салфетки",
                     "napkins"],
        },
    },
}

# CATALOG is WATCHLIST; the split exists only so callers have one stable name to import.
CATALOG = dict(WATCHLIST)

# ── Retailer normalisation ──────────────────────────────────────────────────
# Feeds spell the same chain many ways. Everything keys on the normalised display
# name; the raw string never reaches a key. Lookup is on the LOWERCASED raw value.
RETAILER_ALIASES = {
    "lidl": "Lidl", "lidl bg": "Lidl", "lidl.bg": "Lidl", "lidl bulgaria": "Lidl",
    "kaufland": "Kaufland", "kaufland bg": "Kaufland", "kaufland.bg": "Kaufland",
    "billa": "Billa", "billa bg": "Billa", "billa.bg": "Billa",
    "metro": "Metro", "metro bg": "Metro", "metro bulgaria": "Metro",
    "metro cash & carry": "Metro", "метро": "Metro",
    "technopolis": "Technopolis", "технополис": "Technopolis",
    "zora": "Zora", "зора": "Zora", "technomarket": "Technomarket",
    "jysk": "JYSK", "ikea": "IKEA",
    "amazon": "Amazon.de", "amazon.de": "Amazon.de", "amazon de": "Amazon.de",
    "silabg": "silabg", "silabg.com": "silabg", "sila bg": "silabg",
    "mydealz": "mydealz",
    "fantastico": "Fantastico", "фантастико": "Fantastico",
    "t market": "T MARKET", "tmarket": "T MARKET",
}

# Email section order. Retailers not listed here sort after these, alphabetically.
# Within a retailer, items sort Strong Buy first then Fair, by rank_score.
RETAILER_ORDER = [
    "Metro", "Kaufland", "Lidl", "Billa", "Fantastico", "T MARKET",
    "silabg", "Amazon.de", "Technopolis", "Technomarket", "Zora",
    "JYSK", "IKEA", "mydealz",
]

# ── Unit vocabulary ─────────────────────────────────────────────────────────
# Maps every spelling a source may use to a canonical raw unit. match.parse_qty then
# converts to the sku's unit via UNIT_TO_BASE: g -> kg, ml -> L, pcs -> pc.
# Keys are matched against the LOWERCASED, accent-folded token.
UNIT_ALIASES = {
    # mass
    "кг": "kg", "kg": "kg", "kilo": "kg", "kilogram": "kg", "килограм": "kg",
    "г": "g", "гр": "g", "g": "g", "gr": "g", "gram": "g", "грам": "g",
    # volume
    "л": "L", "l": "L", "lt": "L", "liter": "L", "litre": "L", "литър": "L", "литра": "L",
    "мл": "ml", "ml": "ml", "cl": "cl",
    # count
    "бр": "pcs", "бр.": "pcs", "броя": "pcs", "брой": "pcs",
    "pcs": "pcs", "pc": "pcs", "pieces": "pcs", "stuck": "pcs", "stk": "pcs",
    "x": "pcs",
}

# Canonical raw unit -> (base unit, multiplier to reach it). The ONLY place these
# divisors exist. A second copy is how "100 g" becomes "1.09 EUR/kg".
UNIT_TO_BASE = {
    "kg": ("kg", 1.0),
    "g":  ("kg", 0.001),
    "L":  ("L", 1.0),
    "ml": ("L", 0.001),
    "cl": ("L", 0.01),
    "pcs": ("pc", 1.0),
}
