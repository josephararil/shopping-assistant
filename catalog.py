"""catalog.py — DATA ONLY, no logic. The user's watchlist, wishlist and vocabulary.

Separated from config.py deliberately: this is hundreds of lines of data that the user
owns and edits, while config.py holds knobs the pipeline owns. The travel repo's
config.py hit 727 lines by mixing knobs, data and prompts, and became unreviewable.

── WHAT THE USER OWNS ───────────────────────────────────────────────────────────
`par_eur` and `trigger_eur` are USER-OWNED numbers. `par_eur` is the household's
target unit price — the reference every consumable verdict is measured against.
`trigger_eur` is "yes at this price", an absolute pre-commitment that outranks all
discount maths. The pipeline PROPOSES adjustments to par (config.effective_par, clamped
to +/-PAR_DRIFT_MAX) and surfaces a par-review line in the email; it never overwrites
a number here. Weeks 1-4 are calibration: expect to prune this list after run 1.

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

# ── Consumables: recurring purchases with a target UNIT price ────────────────
# unit is exactly one of "kg" | "L" | "pc". par_eur is EUR per that unit.
# bulk_qty is what "buy and stock up" means for this item — it drives the saving
# figure the user actually reasons with ("buy 5 kg, saves EUR 11").
# restock_days is how long a stock-up lasts, and IS the anti-spam TTL for this item.
WATCHLIST = {
    "food.salmon_fillet": {
        "class": "consumable", "label": "Salmon fillet / steak", "unit": "kg",
        "par_eur": 12.00, "bulk_qty": 5.0, "restock_days": 90, "freezable": True,
        "bulk_note": "Portion into 400 g bags, freeze; keeps 3 months.",
        "match": {
            "any_of": [["сьомга"], ["salmon"], ["losos"], ["lachs"]],
            "none": ["пастет", "pate", "котк", "cat", "куче", "dog",
                     "пушен", "smoked", "gerauchert", "консерв", "spread", "храна"],
        },
    },
    "food.chicken_breast": {
        "class": "consumable", "label": "Chicken breast fillet", "unit": "kg",
        "par_eur": 6.00, "bulk_qty": 6.0, "restock_days": 60, "freezable": True,
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
        "par_eur": 9.00, "bulk_qty": 5.0, "restock_days": 180, "freezable": False,
        "bulk_note": "Keeps 2 years unopened; store dark and cool.",
        "match": {
            "any_of": [["маслиново", "масло"], ["olive", "oil"], ["olivenol"],
                       ["зехтин"]],
            "none": ["сапун", "soap", "козметик", "cosmetic", "маслини", "olives",
                     "спрей", "spray", "шампоан", "shampoo"],
        },
    },
    "food.coffee_beans": {
        "class": "consumable", "label": "Coffee beans / ground coffee", "unit": "kg",
        "par_eur": 14.00, "bulk_qty": 3.0, "restock_days": 120, "freezable": False,
        "bulk_note": "Whole beans keep 6 months sealed; ground goes flat in weeks.",
        "match": {
            "any_of": [["кафе", "зърна"], ["coffee", "beans"], ["kaffeebohnen"],
                       ["кафе", "мляно"], ["coffee", "ground"]],
            "none": ["капсул", "capsule", "dolce", "nespresso", "разтворим",
                     "instant", "3in1", "машина", "machine", "мляко", "milk"],
        },
    },
    "supp.whey_protein": {
        # The named annual-promo case: restock_days ~300 so the yearly silabg promo
        # re-alerts on its own cycle instead of being suppressed by a global TTL.
        "class": "consumable", "label": "Whey protein powder", "unit": "kg",
        "par_eur": 19.00, "bulk_qty": 50.0, "restock_days": 300, "freezable": False,
        "bulk_note": "Sealed tubs keep 18-24 months. The yearly promo is the buy window.",
        "match": {
            "any_of": [["whey"], ["протеин", "whey"], ["суроватъчен"], ["wheyprotein"]],
            "none": ["bar", "барче", "шейк", "готов", "ready", "drink", "напитка",
                     "vegan", "веган", "casein", "казеин", "gainer", "гейнър"],
        },
    },
    "house.laundry_gel": {
        "class": "consumable", "label": "Laundry detergent gel", "unit": "L",
        "par_eur": 4.50, "bulk_qty": 10.0, "restock_days": 120, "freezable": False,
        "bulk_note": "Indefinite shelf life; only storage space limits the buy.",
        "match": {
            "any_of": [["течен", "перилен"], ["liquid", "detergent"], ["waschgel"],
                       ["гел", "пране"], ["laundry", "gel"]],
            "none": ["омекотител", "softener", "капсул", "capsule", "podove",
                     "прах", "powder", "съдомиял", "dishwasher", "препарат", "съдове"],
        },
    },
    # ── Bulk food: meat and fish ──────────────────────────────────────────
    "food.pork_meat": {
        "class": "consumable", "label": "Pork (shoulder/leg), bulk cut", "unit": "kg",
        "par_eur": 4.80, "bulk_qty": 8.0, "restock_days": 75, "freezable": True,
        "bulk_note": "Portion and freeze in 500 g bags; keeps 4 months.",
        "match": {
            "any_of": [["свинско", "месо"], ["pork"], ["schweinefleisch"],
                       ["свинско", "филе"]],
            "none": ["котк", "cat", "куче", "dog", "храна", "консерв", "пастет",
                     "pate", "салам", "наденица", "sausage", "бекон", "bacon"],
        },
    },
    "food.beef_mince": {
        "class": "consumable", "label": "Beef mince", "unit": "kg",
        "par_eur": 7.50, "bulk_qty": 4.0, "restock_days": 60, "freezable": True,
        "bulk_note": "Freeze in 500 g flat portions; keeps 4 months.",
        "match": {
            "any_of": [["телешка", "кайма"], ["beef", "mince"], ["rinderhack"]],
            "none": ["свин", "пиле", "chicken", "кюфте", "meatball", "готово",
                     "ready", "консерв", "canned"],
        },
    },
    "food.turkey_breast": {
        "class": "consumable", "label": "Turkey breast fillet", "unit": "kg",
        "par_eur": 6.50, "bulk_qty": 5.0, "restock_days": 60, "freezable": True,
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
        "par_eur": 7.50, "bulk_qty": 4.0, "restock_days": 90, "freezable": True,
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
        "class": "consumable", "label": "Rice", "unit": "kg",
        "par_eur": 1.20, "bulk_qty": 10.0, "restock_days": 180, "freezable": False,
        "bulk_note": "Shelf-stable; buy a 10 kg bag and store dry.",
        "match": {
            "any_of": [["ориз"], ["rice"], ["reis"]],
            "none": ["пудинг", "pudding", "десерт", "dessert", "вафли", "cakes",
                     "суши", "sushi"],
        },
    },
    "food.pasta": {
        "class": "consumable", "label": "Pasta", "unit": "kg",
        "par_eur": 1.10, "bulk_qty": 8.0, "restock_days": 180, "freezable": False,
        "bulk_note": "Shelf-stable for a year+; stock the whole shelf life.",
        "match": {
            "any_of": [["паста"], ["pasta"], ["nudeln"], ["макарони"]],
            "none": ["зъби", "teeth", "zahnpasta", "zahncreme", "сос", "sauce",
                     "salsa"],
        },
    },
    "food.flour": {
        "class": "consumable", "label": "Wheat flour", "unit": "kg",
        "par_eur": 0.75, "bulk_qty": 10.0, "restock_days": 180, "freezable": False,
        "bulk_note": "Shelf-stable; keep sealed against weevils.",
        "match": {
            "any_of": [["брашно"], ["flour"], ["mehl"]],
            "none": ["бебешко", "baby", "формула", "formula"],
        },
    },
    "food.sugar": {
        "class": "consumable", "label": "White sugar", "unit": "kg",
        "par_eur": 1.00, "bulk_qty": 10.0, "restock_days": 180, "freezable": False,
        "bulk_note": "Shelf-stable indefinitely.",
        "match": {
            "any_of": [["захар"], ["sugar"], ["zucker"]],
            "none": ["заместител", "substitute", "стевия", "stevia", "сорбитол",
                     "sorbitol"],
        },
    },
    "food.lentils": {
        "class": "consumable", "label": "Lentils, dried", "unit": "kg",
        "par_eur": 1.80, "bulk_qty": 5.0, "restock_days": 180, "freezable": False,
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
        "par_eur": 2.00, "bulk_qty": 5.0, "restock_days": 180, "freezable": False,
        "bulk_note": "Shelf-stable for years dry.",
        "match": {
            # "beans" alone also appears in "coffee beans" -- veto it here so this
            # rule never steals food.coffee_beans's match.
            "any_of": [["боб"], ["beans"], ["bohnen"], ["сух", "боб"]],
            "none": ["кафе", "coffee", "зелен", "green", "консерв", "canned",
                     "tinned"],
        },
    },
    "food.oats": {
        "class": "consumable", "label": "Rolled oats", "unit": "kg",
        "par_eur": 1.50, "bulk_qty": 6.0, "restock_days": 150, "freezable": False,
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
        # 400 g tin at ~0.55 EUR -> ~1.35 EUR/kg; par set a little under that.
        "class": "consumable", "label": "Tinned tomatoes (chopped/peeled)", "unit": "kg",
        "par_eur": 1.30, "bulk_qty": 12.0, "restock_days": 150, "freezable": False,
        "bulk_note": "Shelf-stable for 2+ years; stack a case of tins.",
        "match": {
            "any_of": [["домати", "консерва"], ["tinned", "tomatoes"],
                       ["tomaten", "dose"], ["домати", "белени"]],
            "none": ["пресни", "fresh", "пюре", "puree", "кетчуп", "ketchup",
                     "сос", "sauce"],
        },
    },
    "food.tuna_tinned": {
        # 160 g tin at ~1.20 EUR -> ~7.50 EUR/kg tin weight; par a touch under that.
        "class": "consumable", "label": "Tinned tuna", "unit": "kg",
        "par_eur": 7.00, "bulk_qty": 3.0, "restock_days": 150, "freezable": False,
        "bulk_note": "Shelf-stable for years; stock a case of tins.",
        "match": {
            "any_of": [["риба", "тон"], ["tuna"], ["thunfisch"], ["тон", "консерва"]],
            "none": ["котк", "cat", "куче", "dog", "храна", "пастет", "pate",
                     "прясна", "fresh", "стек", "steak"],
        },
    },
    "food.sweetcorn_tinned": {
        # 340 g tin at ~0.80 EUR -> ~2.35 EUR/kg; par a touch under that.
        "class": "consumable", "label": "Tinned sweetcorn", "unit": "kg",
        "par_eur": 2.20, "bulk_qty": 4.0, "restock_days": 150, "freezable": False,
        "bulk_note": "Shelf-stable for years; stock a case of tins.",
        "match": {
            "any_of": [["царевица", "консерва"], ["sweetcorn"], ["mais", "dose"],
                       ["сладка", "царевица"]],
            "none": ["пуканки", "popcorn", "замразена", "frozen", "прясна",
                     "fresh"],
        },
    },
    # ── Bulk food: dairy and eggs ─────────────────────────────────────────
    "food.butter": {
        # 250 g pack at ~2.20 EUR -> ~8.80 EUR/kg; par a touch under that.
        "class": "consumable", "label": "Butter", "unit": "kg",
        "par_eur": 8.50, "bulk_qty": 2.0, "restock_days": 90, "freezable": True,
        "bulk_note": "Freezes well; buy several packs when on promo.",
        "match": {
            "any_of": [["краве", "масло"], ["butter"], ["buttermilch"]],
            "none": ["маргарин", "margarine", "олио", "растителна", "vegetable",
                     "spread", "намазка", "topping"],
        },
    },
    "food.cheese_hard": {
        "class": "consumable", "label": "Hard cheese (cheddar/gouda/parmesan)", "unit": "kg",
        "par_eur": 11.00, "bulk_qty": 2.0, "restock_days": 60, "freezable": True,
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
        "par_eur": 9.00, "bulk_qty": 2.0, "restock_days": 45, "freezable": True,
        "bulk_note": "Vacuum-wrap and freeze in 250 g blocks; keeps 2 months.",
        "match": {
            "any_of": [["кашкавал"], ["kashkaval"]],
            "none": ["крема", "cream", "топено", "processed", "намазка", "spread",
                     "чедър", "cheddar"],
        },
    },
    "food.milk": {
        "class": "consumable", "label": "Milk (fresh or UHT)", "unit": "L",
        "par_eur": 0.90, "bulk_qty": 12.0, "restock_days": 30, "freezable": False,
        "bulk_note": "Only UHT stacks; fresh milk buy weekly at this par.",
        "match": {
            "any_of": [["прясно", "мляко"], ["fresh", "milk"], ["frischmilch"],
                       ["uht", "мляко"]],
            "none": ["кисело", "yoghurt", "йогурт", "сухо", "powder", "бебешко",
                     "baby", "формула", "formula", "шоколадово", "chocolate",
                     "какао", "cocoa", "овесено", "oat", "соево", "soy",
                     "бадемово", "almond"],
        },
    },
    "food.yoghurt": {
        "class": "consumable", "label": "Plain yoghurt", "unit": "kg",
        "par_eur": 2.20, "bulk_qty": 6.0, "restock_days": 21, "freezable": False,
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
        "par_eur": 0.18, "bulk_qty": 30.0, "restock_days": 30, "freezable": False,
        "bulk_note": "Buy ~30 at a time; keep refrigerated 4-5 weeks.",
        "match": {
            "any_of": [["яйца"], ["eggs"], ["eier"]],
            "none": ["шоколадови", "chocolate", "играчка", "toy", "велик",
                     "easter", "прах", "powder", "майонеза", "mayo"],
        },
    },
    # ── Bulk food: oils and condiments ───────────────────────────────────
    "food.sunflower_oil": {
        "class": "consumable", "label": "Sunflower oil", "unit": "L",
        "par_eur": 1.70, "bulk_qty": 10.0, "restock_days": 180, "freezable": False,
        "bulk_note": "Shelf-stable for a year+; buy the case.",
        "match": {
            "any_of": [["слънчогледово", "масло"], ["sunflower", "oil"],
                       ["sonnenblumenol"]],
            "none": ["маслиново", "olive", "зехтин", "рапично", "rapeseed",
                     "сапун", "soap", "козметик", "cosmetic"],
        },
    },
    "food.vinegar": {
        "class": "consumable", "label": "Vinegar", "unit": "L",
        "par_eur": 0.90, "bulk_qty": 5.0, "restock_days": 365, "freezable": False,
        "bulk_note": "Shelf-stable indefinitely.",
        "match": {
            "any_of": [["оцет"], ["vinegar"], ["essig"]],
            "none": ["почистващ", "cleaning", "препарат", "clean"],
        },
    },
    "food.honey": {
        # 500 g jar at ~3.50 EUR -> ~7.00 EUR/kg.
        "class": "consumable", "label": "Honey", "unit": "kg",
        "par_eur": 7.00, "bulk_qty": 3.0, "restock_days": 180, "freezable": False,
        "bulk_note": "Shelf-stable indefinitely; buy several jars.",
        "match": {
            "any_of": [["мед", "пчелен"], ["honey"], ["honig"],
                       ["натурален", "мед"]],
            "none": ["сапун", "soap", "козметик", "cosmetic", "шампоан",
                     "shampoo", "бонбони", "candy", "сладки", "desert"],
        },
    },
    # ── Bulk food: nuts, dried fruit, frozen veg, coffee/tea ─────────────
    "food.walnuts": {
        "class": "consumable", "label": "Walnuts, shelled", "unit": "kg",
        "par_eur": 7.50, "bulk_qty": 2.0, "restock_days": 120, "freezable": True,
        "bulk_note": "Freeze to stop rancidity if stocking beyond 2 months.",
        "match": {
            "any_of": [["орехи"], ["walnuts"], ["walnusse"]],
            "none": ["масло", "butter", "паста", "spread", "шоколад",
                     "chocolate"],
        },
    },
    "food.almonds": {
        "class": "consumable", "label": "Almonds, shelled", "unit": "kg",
        "par_eur": 9.50, "bulk_qty": 2.0, "restock_days": 120, "freezable": True,
        "bulk_note": "Freeze to stop rancidity if stocking beyond 2 months.",
        "match": {
            "any_of": [["бадеми"], ["almonds"], ["mandeln"]],
            "none": ["масло", "butter", "мляко", "milk", "паста", "spread",
                     "есенция", "extract", "шоколад", "chocolate"],
        },
    },
    "food.frozen_vegetables": {
        "class": "consumable", "label": "Frozen mixed/single vegetables", "unit": "kg",
        "par_eur": 2.20, "bulk_qty": 6.0, "restock_days": 60, "freezable": True,
        "bulk_note": "Already frozen; just needs freezer space.",
        "match": {
            "any_of": [["замразени", "зеленчуци"], ["frozen", "vegetables"],
                       ["tiefkuhlgemuse"], ["замразен", "микс"]],
            "none": ["плодове", "fruit", "пица", "pizza", "готово", "ready",
                     "супа", "soup"],
        },
    },
    "food.tea": {
        # A 40-50 g box of bags at ~1.50-1.80 EUR runs ~30-40 EUR/kg by weight;
        # that is the real economics of bagged tea, not a data-entry error.
        "class": "consumable", "label": "Tea (bagged or loose)", "unit": "kg",
        "par_eur": 30.00, "bulk_qty": 0.5, "restock_days": 180, "freezable": False,
        "bulk_note": "Shelf-stable; buy a few boxes at a genuinely good promo.",
        "match": {
            "any_of": [["чай"], ["tea"], ["tee"]],
            "none": ["сапун", "soap", "масло", "oil", "свещ", "candle",
                     "козметик", "cosmetic", "чайник", "teapot", "чаша", "cup"],
        },
    },
    # ── Household consumables ────────────────────────────────────────────
    "house.dishwasher_tablets": {
        # Box of 60 at ~9 EUR -> ~0.15 EUR/tablet.
        "class": "consumable", "label": "Dishwasher tablets", "unit": "pc",
        "par_eur": 0.15, "bulk_qty": 120.0, "restock_days": 120, "freezable": False,
        "bulk_note": "Shelf-stable; two boxes covers 4 months of daily use.",
        "match": {
            "any_of": [["таблетки", "съдомиялна"], ["dishwasher", "tablets"],
                       ["spulmaschinentabs"], ["капсули", "съдомиялна"]],
            "none": ["сол", "salt", "гланц", "rinse"],
        },
    },
    "house.laundry_capsules": {
        # Box of 30 at ~7.50 EUR -> ~0.25 EUR/capsule.
        "class": "consumable", "label": "Laundry capsules/pods", "unit": "pc",
        "par_eur": 0.22, "bulk_qty": 90.0, "restock_days": 90, "freezable": False,
        "bulk_note": "Shelf-stable; three boxes covers ~3 months.",
        "match": {
            "any_of": [["капсули", "пране"], ["laundry", "capsules"],
                       ["waschkapseln"], ["капсули", "перилен"]],
            "none": ["омекотител", "softener", "съдомиялна", "dishwasher",
                     "прах", "powder", "гел", "gel"],
        },
    },
    "house.fabric_softener": {
        "class": "consumable", "label": "Fabric softener", "unit": "L",
        "par_eur": 2.20, "bulk_qty": 5.0, "restock_days": 120, "freezable": False,
        "bulk_note": "Shelf-stable; buy several bottles when cheap.",
        "match": {
            "any_of": [["омекотител", "пране"], ["fabric", "softener"],
                       ["weichspuler"], ["омекотител", "дрехи"]],
            "none": ["съдомиялна", "dishwasher", "прах", "powder", "капсул",
                     "capsule", "гел", "gel"],
        },
    },
    "house.toilet_paper": {
        # 10-roll pack at ~4.50 EUR -> ~0.45 EUR/roll.
        "class": "consumable", "label": "Toilet paper", "unit": "pc",
        "par_eur": 0.40, "bulk_qty": 40.0, "restock_days": 90, "freezable": False,
        "bulk_note": "Shelf-stable; buy 4 packs at a time.",
        "match": {
            "any_of": [["тоалетна", "хартия"], ["toilet", "paper"],
                       ["toilettenpapier"]],
            "none": ["кухненска", "kitchen", "влажни", "wet", "салфетки",
                     "napkins"],
        },
    },
    "house.kitchen_roll": {
        "class": "consumable", "label": "Kitchen roll / paper towels", "unit": "pc",
        "par_eur": 1.00, "bulk_qty": 12.0, "restock_days": 90, "freezable": False,
        "bulk_note": "Shelf-stable; buy a bulk pack of rolls.",
        "match": {
            "any_of": [["кухненска", "хартия"], ["kitchen", "roll"],
                       ["kuchenrolle"], ["кухненски", "рула"]],
            "none": ["тоалетна", "toilet", "влажни", "wet", "салфетки",
                     "napkins"],
        },
    },
    "house.bin_bags": {
        # Box of 20 at ~3.00 EUR -> ~0.15 EUR/bag.
        "class": "consumable", "label": "Bin bags", "unit": "pc",
        "par_eur": 0.12, "bulk_qty": 60.0, "restock_days": 120, "freezable": False,
        "bulk_note": "Shelf-stable; buy 3 boxes at a time.",
        "match": {
            "any_of": [["чували", "боклук"], ["bin", "bags"], ["mullbeutel"],
                       ["торбички", "боклук"]],
            "none": ["прахосмукачка", "vacuum", "филтър", "filter", "компост",
                     "compost"],
        },
    },
    "house.all_purpose_cleaner": {
        "class": "consumable", "label": "All-purpose cleaner", "unit": "L",
        "par_eur": 2.50, "bulk_qty": 4.0, "restock_days": 90, "freezable": False,
        "bulk_note": "Shelf-stable; buy a few bottles/refills.",
        "match": {
            "any_of": [["универсален", "препарат"], ["all", "purpose", "cleaner"],
                       ["allzweckreiniger"], ["почистващ", "спрей"]],
            "none": ["съдомиялна", "dishwasher", "пране", "laundry", "тоалетна",
                     "toilet"],
        },
    },
    "house.shampoo": {
        # 250 ml bottle at ~2.50 EUR -> ~10 EUR/L; par set a touch under that.
        "class": "consumable", "label": "Shampoo", "unit": "L",
        "par_eur": 8.00, "bulk_qty": 2.0, "restock_days": 90, "freezable": False,
        "bulk_note": "Shelf-stable; buy 2-3 large bottles at a good promo.",
        "match": {
            "any_of": [["шампоан"], ["shampoo"]],
            "none": ["балсам", "conditioner", "куче", "dog", "коте", "cat",
                     "животни", "pets", "боя", "dye"],
        },
    },
    "house.toothpaste": {
        # A tube at a good promo runs ~1 EUR.
        "class": "consumable", "label": "Toothpaste", "unit": "pc",
        "par_eur": 1.00, "bulk_qty": 6.0, "restock_days": 120, "freezable": False,
        "bulk_note": "Shelf-stable; buy 6 tubes at a good promo.",
        "match": {
            "any_of": [["паста", "зъби"], ["toothpaste"], ["zahnpasta"]],
            "none": ["четка", "brush", "конец", "floss", "уста", "mouthwash"],
        },
    },
    "baby.nappies": {
        # A pack of 44 at ~11 EUR -> ~0.25 EUR/nappy; par set a touch under that.
        "class": "consumable", "label": "Nappies", "unit": "pc",
        "par_eur": 0.20, "bulk_qty": 176.0, "restock_days": 45, "freezable": False,
        "bulk_note": "Buy 4 packs at a time; check size before stocking up.",
        "match": {
            "any_of": [["пелени"], ["nappies"], ["diapers"], ["windeln"]],
            "none": ["куче", "dog", "коте", "cat", "животни", "pets",
                     "кърпички", "wipes"],
        },
    },
    "baby.wipes": {
        # A pack of 80 at ~2.00 EUR -> ~0.025 EUR/wipe.
        "class": "consumable", "label": "Baby wipes", "unit": "pc",
        "par_eur": 0.02, "bulk_qty": 640.0, "restock_days": 60, "freezable": False,
        "bulk_note": "Buy 8 packs at a time; shelf-stable sealed.",
        "match": {
            "any_of": [["мокри", "кърпички"], ["wet", "wipes"], ["feuchttucher"],
                       ["бебешки", "кърпички"]],
            "none": ["дезинфекциращи", "disinfect", "повърхност", "surface",
                     "тоалетна", "toilet", "пелени", "nappies", "diapers"],
        },
    },
}

# ── Durables: one-off purchases with an absolute "yes at this price" trigger ──
# No par_eur and no unit: a durable is judged on discount against a CREDIBLE reference
# plus absolute saving, or bought outright at trigger_eur. Because there is no
# user-owned par, durables get no `user_par` evidence leg — a lone retailer claim
# leaves them far below MIN_EVIDENCE_STRONG, which is the point.
WISHLIST = {
    "av.sony_xm5": {
        "class": "durable", "label": "Sony WH-1000XM5 headphones",
        "trigger_eur": 200.00, "restock_days": 730,
        "match": {
            "any_of": [["sony", "xm5"], ["sony", "wh1000xm5"], ["wh1000xm5"],
                       ["sony", "wh", "1000xm5"]],
            "none": ["case", "hulle", "калъф", "pads", "earpads", "cable", "кабел",
                     "stand", "стойка", "xm4", "xm3"],
        },
    },
    "tech.robot_vacuum": {
        "class": "durable", "label": "Robot vacuum with mop + self-empty base",
        "trigger_eur": 220.00, "restock_days": 1825,
        "match": {
            "any_of": [["робот", "прахосмукачка"], ["robot", "vacuum"],
                       ["saugroboter"], ["roborock"], ["dreame"], ["robotska"]],
            "none": ["чанта", "bag", "торбичк", "филтър", "filter", "четка",
                     "brush", "мопа", "части", "spare", "accessor"],
        },
    },
    "tech.nas_hdd_4tb": {
        "class": "durable", "label": "NAS-grade 4 TB hard drive",
        "trigger_eur": 90.00, "restock_days": 365,
        "match": {
            "any_of": [["ironwolf", "4tb"], ["wd", "red", "4tb"], ["nas", "4tb"],
                       ["hdd", "4tb"], ["4tb", "nas"]],
            "none": ["ssd", "външен", "external", "usb", "кутия", "enclosure",
                     "2tb", "8tb", "docking"],
        },
    },
    "kitchen.airfryer": {
        "class": "durable", "label": "Air fryer, 5 L+ dual or single basket",
        "trigger_eur": 90.00, "restock_days": 1825,
        "match": {
            "any_of": [["airfryer"], ["air", "fryer"], ["heisluftfritteuse"],
                       ["горещ", "въздух", "фритюрник"], ["ninja", "airfryer"]],
            "none": ["кошница", "basket", "части", "spare", "accessor", "хартия",
                     "paper", "liner", "мазнина", "oil"],
        },
    },
    # ── Computing and phones ────────────────────────────────────────────
    "tech.laptop": {
        "class": "durable", "label": "Laptop, mid-range (8GB+ RAM / 256GB+ SSD)",
        "trigger_eur": 450.00, "restock_days": 1825,
        "match": {
            "any_of": [["лаптоп"], ["laptop"], ["notebook"]],
            "none": ["чанта", "калъф", "case", "зарядно", "charger", "адаптер",
                     "adapter", "стойка", "stand", "раница", "backpack", "sleeve",
                     "аксесоар", "accessory"],
        },
    },
    "tech.tablet": {
        "class": "durable", "label": "Tablet, mid-range",
        "trigger_eur": 180.00, "restock_days": 1460,
        "match": {
            "any_of": [["таблет"], ["tablet"]],
            "none": ["хапче", "лекарство", "витамин", "vitamin", "калъф", "case",
                     "стойка", "stand", "фолио", "protector", "стъкло"],
        },
    },
    "tech.phone": {
        "class": "durable", "label": "Smartphone, mid-range",
        "trigger_eur": 220.00, "restock_days": 1095,
        "match": {
            "any_of": [["телефон"], ["smartphone"], ["phone"]],
            "none": ["калъф", "case", "стъкло", "screen", "protector", "зарядно",
                     "charger", "кабел", "cable", "поставка", "holder"],
        },
    },
    "av.tv": {
        "class": "durable", "label": "TV, 50-55\" 4K",
        "trigger_eur": 280.00, "restock_days": 1825,
        "match": {
            "any_of": [["телевизор"], ["fernseher"], ["smart", "tv"], ["led", "tv"]],
            "none": ["стойка", "stand", "конзола", "wall", "mount", "стена",
                     "дистанционно", "remote", "тонколони", "soundbar", "калъф",
                     "case", "кутия", "box", "аксесоар", "accessory"],
        },
    },
    "tech.ereader": {
        "class": "durable", "label": "E-reader (Kindle/Kobo)",
        "trigger_eur": 75.00, "restock_days": 1825,
        "match": {
            "any_of": [["amazon", "kindle"], ["kobo"], ["електронен", "четец"],
                       ["e", "reader"]],
            "none": ["калъф", "case", "cover", "фолио", "protector", "стойка",
                     "stand", "играчка", "toy"],
        },
    },
    # ── Kitchen appliances ───────────────────────────────────────────────
    "kitchen.coffee_machine": {
        "class": "durable", "label": "Espresso / bean-to-cup coffee machine",
        "trigger_eur": 200.00, "restock_days": 1825,
        "match": {
            "any_of": [["кафемашина"], ["espresso", "machine"], ["кафе", "машина"],
                       ["kaffeemaschine"], ["nespresso", "машина"]],
            "none": ["капсули", "capsules", "хартиен", "filter", "филтър",
                     "почистващ", "descaler", "entkalker", "чашки", "cups",
                     "накип", "descale", "играчка", "toy", "резервен", "spare",
                     "part"],
        },
    },
    "kitchen.stand_mixer": {
        "class": "durable", "label": "Stand mixer / food processor",
        "trigger_eur": 100.00, "restock_days": 1825,
        "match": {
            "any_of": [["кухненски", "робот"], ["stand", "mixer"],
                       ["food", "processor"], ["kuchenmaschine"],
                       ["миксер", "кухненски"]],
            "none": ["приставка", "attachment", "купа", "bowl", "резервна",
                     "резервен", "spare", "part", "аксесоар", "accessory"],
        },
    },
    "kitchen.multicooker": {
        "class": "durable", "label": "Pressure cooker / multicooker",
        "trigger_eur": 55.00, "restock_days": 1825,
        "match": {
            "any_of": [["мултикукър"], ["multicooker"], ["pressure", "cooker"],
                       ["instant", "pot"]],
            "none": ["уплътнение", "гума", "gasket", "капак", "lid", "spare",
                     "part", "резервен", "измервателна"],
        },
    },
    # ── Large appliances ─────────────────────────────────────────────────
    "house.dishwasher": {
        "class": "durable", "label": "Dishwasher, built-in or freestanding",
        "trigger_eur": 280.00, "restock_days": 1825,
        "match": {
            "any_of": [["съдомиялна"], ["dishwasher"], ["spulmaschine"]],
            "none": ["таблетки", "tablets", "капсули", "capsules", "гел", "gel",
                     "сол", "salt", "гланц", "rinse", "препарат", "detergent",
                     "части", "spare", "филтър", "filter"],
        },
    },
    "house.washing_machine": {
        "class": "durable", "label": "Washing machine, 7-8 kg",
        "trigger_eur": 280.00, "restock_days": 1825,
        "match": {
            "any_of": [["перална", "машина"], ["washing", "machine"],
                       ["waschmaschine"], ["перална"]],
            "none": ["прах", "powder", "капсули", "capsules", "гел", "gel",
                     "омекотител", "softener", "маркуч", "hose", "филтър",
                     "filter", "части", "spare", "стойка", "stand"],
        },
    },
    "house.stick_vacuum": {
        "class": "durable", "label": "Upright / cordless stick vacuum cleaner",
        "trigger_eur": 100.00, "restock_days": 1825,
        "match": {
            "any_of": [["прахосмукачка", "безжична"], ["stick", "vacuum"],
                       ["upright", "vacuum"], ["akkusauger"],
                       ["прахосмукачка", "стик"]],
            "none": ["чанта", "bag", "торбичк", "филтър", "filter", "четка",
                     "brush", "маркуч", "hose", "части", "spare", "робот",
                     "robot"],
        },
    },
    "house.chest_freezer": {
        "class": "durable", "label": "Chest freezer, 200 L+",
        "trigger_eur": 200.00, "restock_days": 1825,
        "match": {
            "any_of": [["фризер", "ковчег"], ["chest", "freezer"],
                       ["gefriertruhe"]],
            "none": ["хладилник", "fridge", "части", "spare", "филтър", "filter"],
        },
    },
    # ── Family / DIY ─────────────────────────────────────────────────────
    "baby.car_seat": {
        "class": "durable", "label": "Child car seat",
        "trigger_eur": 90.00, "restock_days": 1095,
        "match": {
            "any_of": [["столче", "кола"], ["car", "seat"], ["autositz"]],
            "none": ["хранене", "количка", "stroller", "играчка", "toy", "калъф",
                     "cover", "велосипед", "bike", "части", "spare"],
        },
    },
    "tools.cordless_diy": {
        "class": "durable", "label": "Cordless drill/driver DIY tool",
        "trigger_eur": 65.00, "restock_days": 1825,
        "match": {
            "any_of": [["акумулаторна", "бормашина"], ["cordless", "drill"],
                       ["akkuschrauber"], ["акумулаторен", "винтоверт"]],
            "none": ["резервна", "spare", "части", "part", "накрайник", "bit"],
        },
    },
}

# Every sku the pipeline knows, consumables first so a consumable rule wins a tie.
CATALOG = {**WATCHLIST, **WISHLIST}

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

# ── Category hints -> class ─────────────────────────────────────────────────
# Used ONLY by the off-list discovery path, to decide whether an unmatched offer is a
# plausible durable. Off-list CONSUMABLE discovery is cut entirely: with no par there
# is nothing to compute a unit price against. A hint that maps to "consumable" or is
# absent therefore kills the lead at `no_sku_match`.
CATEGORY_HINTS = {
    # mydealz <category> values
    "elektronik": "durable",
    "haushalt & wohnen": "durable",
    "computer & zubehor": "durable",
    "gaming": "durable",
    "heimwerken & garten": "durable",
    "lebensmittel & haushalt": "consumable",
    "drogerie & gesundheit": "consumable",
    "beauty & gesundheit": "consumable",
    # broshura.bg / camelcamelcamel-ish groupings
    "electronics": "durable",
    "техника": "durable",
    "електроника": "durable",
    "бяла техника": "durable",
    "мебели": "durable",
    "инструменти": "durable",
    "хранителни стоки": "consumable",
    "хранителни": "consumable",
    "напитки": "consumable",
    "битова химия": "consumable",
    "козметика": "consumable",
}

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
