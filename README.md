# pod2twc — POD listing → in-house (TWC) twin

Turns a POD-app product into its in-house duplicate without the copy/paste.

**Workflow it supports:** build the product in Ninja POD / Printify / Printful / Gelato,
set the retail price there, let it push to Shopify. Then run this. It duplicates the
listing, strips every trace of POD fulfillment, re-attributes it to the partner org,
captures the POD basis cost, links the pair together, and flips which half is live.

Every command is a **dry run** until you add `--commit`.

---

## Install

```bash
cd ~/Dev/pod2twc
python3 -m venv ~/.venvs/pod2twc && ~/.venvs/pod2twc/bin/pip install requests
```

Credentials — either environment variables:

```bash
export SHOPIFY_STORE_DOMAIN=steeple-stitch.myshopify.com   # your .myshopify.com host
export SHOPIFY_ADMIN_TOKEN=shpat_xxxxxxxx
```

…or `~/.config/steeple/shopify.json`:

```json
{ "store_domain": "steeple-stitch.myshopify.com", "admin_token": "shpat_xxxxxxxx" }
```

Required Admin API scopes: `read_products`, `write_products`, `read_inventory`,
`write_inventory`, `read_locations`, `write_publications`.

---

## Use

```bash
P=~/.venvs/pod2twc/bin/python

# what's sitting on the POD side with no in-house twin
$P pod2twc.py list

# see the plan (nothing is written)
$P pod2twc.py clone 15249989992816 --org gcs-athletics --cost 9.00

# do it
$P pod2twc.py clone 15249989992816 --org gcs-athletics --cost 9.00 --commit

# switch which half is live (refuses if the half going live covers less)
$P pod2twc.py flip gcs-property-of-twc --to pod --commit

# every pair + what's missing
$P pod2twc.py audit

# recompute attribution from vendor and repair whatever drifted
$P pod2twc.py metafields
$P pod2twc.py metafields --commit

# record a coverage gap the supplier can't fill
$P pod2twc.py except <ref> --add "White / S" --commit
```

`<ref>` accepts a numeric product id, a `gid://…`, or a handle.

### clone flags

| flag | effect |
|---|---|
| `--org KEY` | required — sets vendor, tag, collection, pickup location, store URL |
| `--cost N` | in-house unit cost per variant — the **fully loaded** cost including labor. **Set this** or the margin workbook reads the POD cost as your cost |
| `--pod-cost N` | true POD cost for the payout basis. Needed when the app pushed cost = retail (Printify does) |
| `--price N` | override retail. Default keeps the POD price, since retail must match across the pair |
| `--title "…"` | override the base title (` TWC` is appended) |
| `--keep-images` | copy the POD mockups too. Default starts with no images so you drop your own in |
| `--keep-skus` | keep the POD-generated SKUs instead of clearing them |
| `--no-flip` | leave the clone DRAFT and the POD listing untouched |

Org keys: `gcs`, `gcs-athletics`, `revive`, `fhcog`, `hoh`, `community-christian`,
`steeple-stitch`. Add more in `config.json`.

---

## What the clone pass actually does, in order

The order matters — several of these break if you reverse them.

1. **`productDuplicate` as DRAFT.** Nothing is customer-visible while the transform runs.
2. **Identity + attribution.** vendor → the org, productType, tag, handle. Vendor is the
   load-bearing one: the collections are vendor-driven smart rules, so setting vendor
   correctly puts the product in the right storefront collection by itself.
3. **Drop the POD-only option.** Ninja POD adds a third option — `Product`, holding the
   blank garment name ("Comfort Colors C1717 Adult 6.1 oz. T-Shirt"). Deleted
   `NON_DESTRUCTIVE`, so it only proceeds if no variant would be lost.
4. **Re-case option values.** POD pushes `GRAPHITE`/`WHITE`; sizes (`2XL`, `YXS`, `5/6`)
   and already-cased values are left alone.
5. **Variants, in one bulk call.** Inventory tracking **off first** — otherwise Shopify
   re-assigns locations underneath you. Then: clear the POD SKU gibberish
   (`19g61mtdqibbb`), set your in-house cost, carry the *real* per-variant weights over
   from the POD listing, `requiresShipping: true`, `inventoryPolicy: DENY`.
6. **Detach POD fulfillment.** This is the step that matters most. A naive duplicate stays
   stocked at the **Ninja POD** location, so an order on your in-house listing routes
   straight back to the POD vendor and you pay POD cost on a shirt you printed. Every
   level at a POD fulfillment-service location is deactivated; the clone is then stocked
   at Shop Pickup – Dayton plus the org's pickup location.
7. **Metafields.** `custom.church_store`, `_name`, `_url` as usual, plus two new ones:
   - `custom.pod_basis_cost` — the POD unit cost, captured at the moment it's on screen.
     That's the payout basis, and it's the number that has been going missing.
   - `custom.pod_source_product` — a product reference back to the POD half, so the pair
     is linked by ID instead of by title matching.
8. **Purge POD mockups** that rode along (alt `ndm:…`, or `ninjapod_media_` in the URL).
9. **Publish** to Online Store, Shop, POS, Meta, Copilot.
10. **Flip** — clone ACTIVE, POD source DRAFT.

## Ninja POD vs Printify sources

The two apps hand you very different products. The script detects which one made the
source (app metafield namespace, then stock location) and adapts.

| | Ninja POD | Printify |
|---|---|---|
| example | `GCS Property Of` | `Cardinals Weekender Tote Bag` |
| state when it lands | DRAFT, vendor `Steeple & Stitch Co.`, no type/tags/metafields | often already ACTIVE with vendor, type, tags and church metafields set |
| extra option | adds **`Product`** holding the blank garment name | none |
| option casing | `GRAPHITE`, `WHITE` | already correct |
| SKU | `19g61mtdqibbb` | `79763280910823629950` |
| inventory | `tracked: false`, CONTINUE, 9999 | **`tracked: true`**, DENY, real count |
| unit cost | real POD cost (11.16 vs 16.16 retail) | **cost = retail (30.00)** — a placeholder |
| app metafields | none | **`printify_custom.printify_product_id`, `.is_personalizable`** |
| image filenames | `ninjapod_media_*.jpg`, alt `ndm:…` | `16626529277276695807_2048.jpg` |

Three of these bite specifically on Printify sources:

1. **`tracked: true`.** Duplicate it as-is, detach it from Printify, and the clone sits at
   quantity 0 with `inventoryPolicy: DENY` — unbuyable, with no error anywhere. Tracking
   is turned off before the detach for exactly this reason.
2. **Cost equals retail.** Printify pushes `unitCost` = the retail price by default. That
   is not a cost, and filing it as `pod_basis_cost` would corrupt the partner payout.
   The script refuses to write the metafield when cost == price and tells you to pass
   `--pod-cost N` with the real number.
3. **`printify_custom` metafields.** These survive a duplicate. Left on the clone, it
   still looks like a Printify product to anything keying on `printify_product_id`. They
   get deleted.

Printify listings also frequently already carry the correct vendor, so `list` and `audit`
identify the POD half by **stock location and app metafields**, not vendor alone —
otherwise every Printify product would be invisible to them.

## `metafields` — making Flow non-load-bearing

Vendor is the single source of truth in this store: the storefront collections are
`VENDOR EQUALS` smart rules, so `custom.church_store`, `_name` and `_url` are a pure
function of vendor. Anything that disagrees is wrong, whatever wrote it.

`metafields` recomputes all three from the org registry and repairs the difference.
It reports rather than guesses when a product's vendor isn't in the registry — those
need the **vendor** fixed first, not the metafields.

```
Attribution drift (expected values derived from vendor)

  ✗ GCS Athletics — Embroidered Cardinal 'Script' Hat TWC
      ACTIVE  vendor='GCS Athletics'
      church_store
        was  gid://shopify/Collection/663849042288
        →    gid://shopify/Collection/665060966768
```

| flag | effect |
|---|---|
| `--fix-names` | also reset `church_store_name`; off by default because some values are deliberate (the hats carry "The Williams Collective") |
| `--fill` | also attribute products that have no attribution at all; off by default so POD source shells are left alone |

Run it after any batch of POD publishing. It caught five misattributed products on
2026-09-08 — four ACTIVE, all pointing at the wrong partner church.

## Pair coverage — the flip guard

A dual-SKU pair is only safe to flip if both halves offer the same colours and sizes.
They often don't: a POD listing may carry one colourway and a narrower size run than the
in-house twin. Flip onto the smaller half and the storefront silently loses variants a
customer could buy the day before.

`audit` reports every mismatched pair, and `flip` **refuses** to switch onto a half that
covers less than the one going dark:

```
  ✗ GCS Basketball -Comfort Colors Adult T-Shirt TWC
      in-house 14 variants  ·  POD 5 variants
      POD half is missing Color: Black
      POD half is missing Size: S, 4XL
```

**Actual variant combinations are compared, not just declared option values.** Declared
options are not enough: a listing can offer Color [Black, White] and Size [S–4XL] while
the grid is missing White/S and White/4XL — every option value present, two variants
absent, and an option-level check calls it clean. That exact case is what a POD app
produces when a colourway is added without the full size run.

Comparison is case-normalised, and options that exist on only one side with a single
value are dropped first — that's Ninja POD's `Product` option holding the blank garment
name, which is packaging, not choice. A one-sided option with real choice in it (a
`Sleeve` the twin doesn't have) *is* reported. When an entire colour or size is absent it
collapses to one line rather than listing every combination.

`--force` overrides the refusal when you mean it — but reach for `except` instead.

### Accepted exceptions

Some gaps are permanent: the supplier simply doesn't make that combination. Forcing past
the guard every time trains you to ignore it, so record the gap instead:

```bash
$P pod2twc.py except gcs-basketball-comfort-colors-adult-t-shirt-twc          # show state
$P pod2twc.py except <ref> --add "White / S" --commit
$P pod2twc.py except <ref> --remove "White / S" --commit
```

Exceptions live in `custom.pair_coverage_exceptions` on the in-house half — a
`list.single_line_text_field` of combination labels in `Colour / Size` order, matched
case- and space-insensitively. `audit` and `flip` skip them and keep firing on everything
else, so a *new* hole still stops a flip.

`except` with no flags prints what's recorded and what isn't, and flags a recorded
exception that is no longer a gap (supplier restocked — safe to remove). Adding a label
that doesn't match a current gap warns rather than silently accepting a typo.

**Recorded so far:** `GCS Basketball … TWC` → `White / S` (Printify has no White small).

## Cost convention — in-house is loaded, POD is not

The two cost figures are not like-for-like and are not meant to be:

| | what it is |
|---|---|
| `pod_basis_cost` | what the POD app charges — blank plus print. The **partner payout basis**. |
| in-house `unitCost` | **fully loaded**, including labor. Set deliberately so shop time is covered rather than absorbed. |

So in-house cost sitting **above** POD basis is normal and correct, not an error. On the GCS
Basketball tee it's 14.00 in-house against 10.93 at Printify. Nothing in this tool flags
that, and nothing should — don't "fix" it.

The partner payout is calculated against the POD basis either way, so which half fulfills
an order changes your own margin, never the org's cut.

## Metafield definitions

`metafieldsSet` writes values regardless, but without definitions they don't show in admin
or appear in Flow. Worth creating once, Settings → Custom data → Products:

| key | type | name |
|---|---|---|
| `custom.pod_basis_cost` | Decimal | POD basis cost |
| `custom.pod_source_product` | Product reference | POD source product |
| `custom.pair_coverage_exceptions` | Single line text (list) | Pair coverage exceptions |

## Weights

The POD listings carry real per-variant weights (0.38 lb, 0.39 lb…). Several existing
in-house twins have weight `0` with `requiresShipping` on, which quietly distorts
shipping rates. The clone pass copies the weights across; `scrub` does not — fix those in
admin or re-clone.

## Two metafield definitions to create

`metafieldsSet` writes the values regardless, but without definitions they won't show in
admin or be pickable in Flow. Worth creating once:

- `custom.pod_basis_cost` — type `number_decimal`, owner Product, name "POD basis cost"
- `custom.pod_source_product` — type `product_reference`, owner Product, name "POD source product"

## Reversing

Nothing is deleted. To undo a clone: `flip <ref> --to pod --commit` to put the POD
listing back on the storefront, then archive or delete the clone in admin.
