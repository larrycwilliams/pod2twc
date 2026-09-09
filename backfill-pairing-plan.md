# POD ↔ in-house pairing plan — DRY RUN

Generated 2026-09-08 from live store data. Nothing has been written.

`--commit` would write `custom.pod_source_product` and `custom.pod_basis_cost` on the in-house (TWC) half of each accepted pair.


## EXACT (11)

Titles match once the TWC suffix is removed. Safe to write.

| in-house half | POD half | app | POD basis | variants |
|---|---|---|---|---|
| GCS - Long-Sleeve Performance T-Shirt TWC | GCS - Long-Sleeve Performance T-Shirt | Ninja POD | 11.18 | **10 → 20** ⚠ |
| GCS Basketball -Comfort Colors Adult T-Shirt TWC *(linked)* | GCS Basketball -Comfort Colors Adult T-Shirt | Printify | 10.93 | **14 → 12** ⚠ |
| GCS Comfort Colors Logo T-Shirt TWC | GCS Comfort Colors Logo T-Shirt | Ninja POD | 11.16 | 7 → 7 |
| GCS Crewneck Youth TWC | GCS Crewneck Youth | Ninja POD | 16.06 | 10 → 10 |
| GCS Logo Mascot Hoodie TWC | GCS Logo Mascot Hoodie | Ninja POD | 16.49 | **16 → 18** ⚠ |
| GCS Logo Tultex T-Shirt - Youth Size TWC | GCS Logo Tultex T-Shirt - Youth Size | Ninja POD | 8.29 | 5 → 5 |
| GCS Logo Tultex T-Shirt TWC | GCS Logo Tultex T-Shirt | Ninja POD | 13.55 | 14 → 14 |
| GCS Logo Youth - Mascot Hoodie TWC | GCS Logo Mascot Hoodie - Youth | Ninja POD | 18.46 | **12 → 5** ⚠ |
| GCS Rabbit Skins Toddler T-shirt TWC | GCS Rabbit Skins Toddler T-shirt | Ninja POD | 10.62 | 5 → 5 |
| GCS Seniors 2027 Short Sleeve & Long Sleeve T-Shirt TWC | GCS Seniors 2027 Short Sleeve & Long Sleeve T-Shirt | Ninja POD | 11.16 | **24 → 26** ⚠ |
| GCS Softstyle Crewneck Sweatshirt TWC | GCS Softstyle Crewneck Sweatshirt | Ninja POD | 16.04 | **14 → 15** ⚠ |

## STRONG (8)

Abbreviated or reworded POD title, same org, same garment. Worth a glance.

| in-house half | POD half | app | POD basis | variants |
|---|---|---|---|---|
| GCS - Basketball Long-Sleeve Performance T-Shirt TWC | GCS Basketball LS | vendor | 14.18 | **14 → 17** ⚠ |
| GCS Property Of -Comfort Colors Adult T-Shirt TWC | GCS Property Of | vendor | 11.16 | 14 → 14 |
| GCS THE CARDINAL WAY Comfort Colors Adult T-Shirt TWC | GCS CARDINAL WAY | vendor | 13.65 | **7 → 14** ⚠ |
| GCS Volleyball -Comfort Colors Adult T-Shirt TWC | GCS Volleyball -CC | vendor | 11.16 | 14 → 14 |
| Revive Church - Comfort Colors - Color Blast T-Shirt TWC | Revive Church Comfort Colors Acid Wash T-Shirt | Ninja POD | 16.61 | 24 → 24 |
| Revive Church Color Blast Crewneck - TWC | ReviveColorBlastCrew | vendor | 31.65 | 30 → 30 |
| Revive Church Tultex T-Shirt TWC | Revive Church Softstyle T-Shirt | Ninja POD | 8.92 | **21 → 24** ⚠ |
| ~~Richardson 112 Trucker Hat - TWC~~ | ~~Custom Patch Trucker Hat — Richardson 112~~ | — | — | **REJECTED** — both halves are in-house |

## REVIEW (4)

Low confidence — confirm or reject each one.

| in-house half | POD half | app | POD basis | variants |
|---|---|---|---|---|
| GCS 'NO FLY ZONE' Adult T-Shirt TWC | GCS NO FLYZONE | vendor | 8.92 | **8 → 25** ⚠ — **ACCEPT** |
| ~~GCS ATHLETICS -Comfort Colors Adult T-Shirt TWC~~ | ~~Steeple & Stitch Co. T-Shirt~~ | — | — | **REJECTED** — generic house tee |
| GCS Ladies Comfort Colors BOW Logo T-Shirt TWC | GCS CC Ladies White Bow | vendor | 11.16 | **14 → 7** ⚠ — **ACCEPT** (White only) |
| ~~GCS Youth Performance Long Sleeve T-Shirt TWC~~ | ~~GCS Long Sleeve Performance Polo~~ | — | — | **REJECTED** — polo ≠ youth LS tee |

## NO MATCH — in-house only (8)

Confirmed in-house: embroidery and patch work, no POD counterpart and no POD fallback intended. Record a quoted POD cost by hand only if a payout basis is needed.

- GCS Athletics — Embroidered Cardinal 'Script' Hat TWC  ·  2 variants  ·  cost 8.00
- GCS Embroidered Cardinal 'Script' Hat TWC  ·  2 variants  ·  cost 8.00
- GCS Embroidered Cardinal Logo Hat TWC  ·  2 variants  ·  cost 8.00
- Richardson 112 Trucker Hat - TWC  ·  18 variants  ·  **no cost, tracking ON**
- GCS Cardinals - Under Armour - Men's Tech™ Polo TWC  ·  6 variants
- GCS Staff-Signature Men's Softshell Bomber Jacket - TWC  ·  6 variants
- GCS Toddler Bella & Canvas Long Sleeve T-Shirt TWC  ·  4 variants
- GCS Youth Girls Crewneck BOW Logo T-Shirt TWC  ·  10 variants

## Summary

- **15 pairs** would be written on `--commit` (18 proposed, 3 rejected)
- **8** of them have a variant-count mismatch and will immediately fail the flip guard — the capacity valve is not usable on those until the halves match
- **8** in-house listings have no POD counterpart at all, by design

## Separate issue — Richardson 112 duplication

Three overlapping listings for the same product line:

| product | status | variants | structure |
|---|---|---|---|
| Richardson 112 Trucker Hat - TWC | ACTIVE | 18 | Color only, no SKUs, no cost, **tracking ON** |
| Custom Patch Trucker Hat — Richardson 112 | DRAFT | 24 | Color / Patch color / Patch material, SKUs `SS112-*` |
| Custom Patch Trucker Hat — Church Bulk Program (Richardson 112) | DRAFT | 2 | Patch material only, SKUs `SS112B-*` |

The two drafts are the fuller build. Decide which listing is the retail one before adding cost data to any of them.
