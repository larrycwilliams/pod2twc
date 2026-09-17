#!/usr/bin/env python3
"""
pod2twc — clone a POD-app product into its in-house "TWC" twin.

Steeple & Stitch Co. dual-SKU workflow:
  1. Create the product in the POD app (Ninja POD / Printify / Printful / Gelato),
     set the retail price there, let it push to Shopify.
  2. Run this script. It duplicates that listing into an in-house twin, strips
     every trace of POD fulfillment, re-attributes it to the partner org, records
     the POD basis cost for the payout math, and links the two together.
  3. Exactly one of the pair is ACTIVE at any time. `flip` switches which.

Everything is a DRY RUN unless you pass --commit.

Auth (either one):
  export SHOPIFY_STORE_DOMAIN=steeple-stitch.myshopify.com
  export SHOPIFY_ADMIN_TOKEN=shpat_xxx
or ~/.config/steeple/shopify.json  ->  {"store_domain": "...", "admin_token": "..."}

Commands
  list                     POD-side candidates that have no in-house twin yet
  show    <ref>            dump what the script sees for one product
  clone   <ref> --org KEY  build the in-house twin
  flip    <ref> --to twc|pod
  audit                    every pair in the store + what's missing
  scrub   <ref>            re-run the strip/attribute pass on an existing twin

<ref> is a numeric product id, a full gid://, or a product handle.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

HERE = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("POD2TWC_CONFIG", HERE / "config.json"))

C_RESET, C_DIM, C_RED, C_GRN, C_YEL, C_CYN, C_BLD = (
    "\033[0m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[36m", "\033[1m",
)
if not sys.stdout.isatty():
    C_RESET = C_DIM = C_RED = C_GRN = C_YEL = C_CYN = C_BLD = ""


# ─────────────────────────────────────────────────────────────── client ──

class Shop:
    def __init__(self, cfg):
        self.cfg = cfg
        domain = os.environ.get("SHOPIFY_STORE_DOMAIN")
        token = os.environ.get("SHOPIFY_ADMIN_TOKEN")
        if not (domain and token):
            f = Path.home() / ".config" / "steeple" / "shopify.json"
            if f.exists():
                d = json.loads(f.read_text())
                domain = domain or d.get("store_domain")
                token = token or d.get("admin_token")
        if not (domain and token):
            sys.exit("No credentials. Set SHOPIFY_STORE_DOMAIN + SHOPIFY_ADMIN_TOKEN "
                     "or create ~/.config/steeple/shopify.json")
        self.url = f"https://{domain}/admin/api/{cfg['api_version']}/graphql.json"
        self.headers = {"X-Shopify-Access-Token": token,
                        "Content-Type": "application/json"}
        self.calls = 0

    def gql(self, query, variables=None, tries=5):
        for attempt in range(tries):
            r = requests.post(self.url, headers=self.headers,
                              json={"query": query, "variables": variables or {}},
                              timeout=60)
            self.calls += 1
            if r.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            body = r.json()
            if "errors" in body:
                msg = json.dumps(body["errors"])
                # Shopify throttles by leaky bucket; back off and retry.
                if "THROTTLED" in msg.upper():
                    time.sleep(2 * (attempt + 1))
                    continue
                raise RuntimeError(f"GraphQL error: {msg}")
            return body["data"]
        raise RuntimeError("Throttled out after retries")

    def mutate(self, name, query, variables, commit):
        """Run a mutation, or print it, depending on --commit."""
        if not commit:
            return {"_dryrun": True}
        data = self.gql(query, variables)
        payload = data.get(name) or {}
        errs = (payload.get("userErrors") or payload.get("mediaUserErrors") or [])
        if errs:
            raise RuntimeError(f"{name} userErrors: {json.dumps(errs)}")
        return payload


# ─────────────────────────────────────────────────────────────── helpers ──

def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^\w\s-]", "", s).strip().lower()
    return re.sub(r"[-\s]+", "-", s)[:255]


def titlecase_option(v: str) -> str:
    """GRAPHITE -> Graphite, but leave 2XL / YXS / S / 5/6 / Tri-Blend alone."""
    if not v or any(ch.isdigit() for ch in v):
        return v
    if len(v) <= 3 and v.isupper():          # S, M, L, XL, YXS
        return v
    if not v.isupper():
        return v
    return " ".join(w.capitalize() for w in v.split(" "))


def money(x):
    return None if x is None else f"{float(x):.2f}"


def note(kind, msg):
    c = {"ok": C_GRN, "warn": C_YEL, "err": C_RED, "info": C_CYN, "dim": C_DIM}[kind]
    sym = {"ok": "✓", "warn": "!", "err": "✗", "info": "·", "dim": " "}[kind]
    print(f"  {c}{sym}{C_RESET} {msg}")


PRODUCT_FIELDS = """
  id title handle status vendor productType tags templateSuffix descriptionHtml
  options { id name position optionValues { id name } }
  metafields(first: 60) { edges { node { id namespace key type value } } }
  media(first: 50) { edges { node { id alt ... on MediaImage { image { url } } } } }
  variants(first: 100) {
    edges { node {
      id title sku price compareAtPrice inventoryPolicy taxable
      selectedOptions { name value }
      inventoryItem {
        id tracked requiresShipping
        measurement { weight { value unit } }
        unitCost { amount }
        inventoryLevels(first: 20) { edges { node { id location { id name } } } }
      }
    } }
  }
"""


def fetch_product(shop, ref):
    gid = resolve_ref(shop, ref)
    q = "query($id: ID!) { product(id: $id) { %s } }" % PRODUCT_FIELDS
    p = shop.gql(q, {"id": gid})["product"]
    if not p:
        sys.exit(f"No product for {ref}")
    p["variants"] = [e["node"] for e in p["variants"]["edges"]]
    p["metafields"] = [e["node"] for e in p["metafields"]["edges"]]
    p["media"] = [e["node"] for e in p["media"]["edges"]]
    for v in p["variants"]:
        v["inventoryItem"]["inventoryLevels"] = [
            e["node"] for e in v["inventoryItem"]["inventoryLevels"]["edges"]]
    return p


def resolve_ref(shop, ref):
    ref = str(ref).strip()
    if ref.startswith("gid://"):
        return ref
    if ref.isdigit():
        return f"gid://shopify/Product/{ref}"
    d = shop.gql("query($h: String!) { productByIdentifier(identifier: {handle: $h}) { id } }",
                 {"h": ref})
    if d.get("productByIdentifier"):
        return d["productByIdentifier"]["id"]
    d = shop.gql('query($q: String!) { products(first: 5, query: $q) { edges { node { id title } } } }',
                 {"q": f'title:"{ref}"'})
    edges = d["products"]["edges"]
    if len(edges) == 1:
        return edges[0]["node"]["id"]
    if not edges:
        sys.exit(f"Nothing matches {ref!r}")
    sys.exit("Ambiguous:\n" + "\n".join(
        f"  {e['node']['id']}  {e['node']['title']}" for e in edges))


def is_pod_media(cfg, m):
    alt = (m.get("alt") or "")
    url = ((m.get("image") or {}).get("url") or "").lower()
    if any(alt.startswith(p) for p in cfg["pod_media_alt_prefixes"]):
        return True
    if any(marker in url for marker in cfg["pod_media_markers"]):
        return True
    # Printify uploads land as long numeric filenames (16626529277276695807_2048.jpg)
    fname = url.split("?")[0].rsplit("/", 1)[-1]
    return any(re.match(p, fname) for p in cfg.get("pod_media_filename_patterns", []))


def app_metafields(cfg, product):
    """Metafields written by the POD app itself — must not survive onto the clone."""
    ns = set(cfg.get("app_metafield_namespaces", []))
    return [m for m in product["metafields"] if m["namespace"] in ns]


def detect_app(cfg, product):
    """Which POD app made this: by app metafield namespace, then by stock location."""
    for m in app_metafields(cfg, product):
        return m["namespace"].replace("_custom", "").replace("_", " ").title()
    for v in product["variants"]:
        for l in v["inventoryItem"]["inventoryLevels"]:
            name = cfg["pod_locations"].get(l["location"]["id"])
            if name:
                return name
    return None


def pod_cost_of(product):
    """Modal non-null unitCost across variants — the POD basis cost.

    Returns (cost, suspect). `suspect` is True when the cost equals the variant's
    own price: Printify pushes cost = retail by default, which is not a real cost
    and must not be filed as the partner payout basis.
    """
    pairs = [(v["inventoryItem"]["unitCost"]["amount"], v["price"])
             for v in product["variants"] if v["inventoryItem"].get("unitCost")]
    if not pairs:
        return None, False
    costs = [c for c, _ in pairs]
    modal = max(set(costs), key=costs.count)
    suspect = all(float(c) == float(p) for c, p in pairs if c == modal)
    return modal, suspect


# ─────────────────────────────────────────────────────────────── clone ──

def build_plan(cfg, src, org, args):
    """Everything the transform will change, computed before anything is written."""
    org_cfg = cfg["orgs"][org]
    base = args.title or strip_pod_noise(src["title"])
    new_title = base if base.endswith(cfg["twc_suffix"]) else base + cfg["twc_suffix"]

    drop_opts = [o for o in src["options"]
                 if len(o["optionValues"]) == 1
                 and o["name"] in cfg["drop_single_value_options"]]

    recase = []
    for o in src["options"]:
        if o in drop_opts:
            continue
        for ov in o["optionValues"]:
            new = titlecase_option(ov["name"])
            if new != ov["name"]:
                recase.append((o, ov, new))

    pod_cost, cost_suspect = pod_cost_of(src)
    if getattr(args, "pod_cost", None) is not None:
        pod_cost, cost_suspect = money(args.pod_cost), False
    house_cost = money(args.cost) if args.cost is not None else None

    keep_locs = set(org_cfg["pickup_locations"]) | {cfg["house_location"]}
    pod_locs = set(cfg["pod_locations"])

    bad_media = [m for m in src["media"] if is_pod_media(cfg, m)]

    return {
        "org": org, "org_cfg": org_cfg,
        "new_title": new_title,
        "new_handle": slugify(new_title),
        "product_type": args.product_type or src["productType"] or "Apparel",
        "drop_options": drop_opts,
        "recase": recase,
        "pod_cost": pod_cost,
        "cost_suspect": cost_suspect,
        "source_app": detect_app(cfg, src),
        "app_metafields": app_metafields(cfg, src),
        "tracked_variants": sum(1 for v in src["variants"]
                                if v["inventoryItem"].get("tracked")),
        "house_cost": house_cost,
        "keep_locations": keep_locs,
        "pod_locations": pod_locs,
        "pod_media": bad_media,
        "clear_skus": not args.keep_skus,
        "include_images": bool(args.keep_images),
    }


def strip_pod_noise(title):
    t = re.sub(r"\s*-\s*copy$", "", title, flags=re.I)
    t = re.sub(r"\s*\(?\bcopy\b\)?$", "", t, flags=re.I)
    return t.strip()


def print_plan(src, plan, cfg):
    o = plan["org_cfg"]
    print(f"\n{C_BLD}SOURCE{C_RESET}  {src['title']}")
    print(f"        {C_DIM}{src['id']}  status={src['status']}  vendor={src['vendor']!r}  "
          f"{len(src['variants'])} variants{C_RESET}")
    print(f"        {C_DIM}POD app: {plan['source_app'] or 'unknown'}{C_RESET}")
    print(f"\n{C_BLD}PLAN{C_RESET}")
    note("info", f"title     → {plan['new_title']}")
    note("info", f"handle    → {plan['new_handle']}")
    note("info", f"vendor    → {o['vendor']}   {C_DIM}(drives the {o['collection_handle']} smart collection){C_RESET}")
    note("info", f"type/tags → {plan['product_type']} / [{o['tag']}]")

    if plan["drop_options"]:
        for op in plan["drop_options"]:
            note("ok", f"drop POD option {op['name']!r} (single value "
                       f"{op['optionValues'][0]['name']!r})")
    if plan["recase"]:
        sample = ", ".join(f"{ov['name']}→{new}" for _, ov, new in plan["recase"][:6])
        note("ok", f"re-case {len(plan['recase'])} option value(s): {sample}")

    if plan["clear_skus"]:
        n = sum(1 for v in src["variants"] if v["sku"])
        note("ok", f"clear {n} POD-generated SKU(s)")

    note("ok", "inventory tracking → off, then detach from POD locations:")
    hits = {}
    for v in src["variants"]:
        for lvl in v["inventoryItem"]["inventoryLevels"]:
            lid = lvl["location"]["id"]
            if lid in plan["pod_locations"] or lid not in plan["keep_locations"]:
                hits.setdefault(lvl["location"]["name"], 0)
                hits[lvl["location"]["name"]] += 1
    for name, n in hits.items():
        note("warn", f"    detach {n} variant level(s) from {C_RED}{name}{C_RESET}")
    for lid in sorted(plan["keep_locations"]):
        note("ok", f"    stock at {cfg.get('location_names', {}).get(lid, lid)}")

    if plan["app_metafields"]:
        names = ", ".join(f"{m['namespace']}.{m['key']}" for m in plan["app_metafields"])
        note("ok", f"strip {len(plan['app_metafields'])} app metafield(s): {names}")

    if plan["tracked_variants"]:
        note("warn", f"{plan['tracked_variants']} variant(s) have inventory TRACKED "
                     f"(Printify does this) — tracking is turned off before the detach, "
                     f"or the clone lands at 0/DENY and can't be bought")

    if plan["cost_suspect"]:
        note("err", f"POD unit cost ({plan['pod_cost']}) equals retail — that's a "
                    f"placeholder, not a real cost. pod_basis_cost NOT written; "
                    f"pass --pod-cost N with the true POD cost.")
    else:
        note("info", f"POD basis cost captured → "
                     f"{plan['pod_cost'] or C_RED + 'NONE FOUND' + C_RESET}")
    if plan["house_cost"]:
        note("info", f"in-house unit cost     → {plan['house_cost']}")
    else:
        note("warn", "no --cost given: in-house unit cost will be left at the POD value "
                     "(margin workbook will read the wrong spread)")

    if plan["pod_media"]:
        verb = "skip" if not plan["include_images"] else "delete after copy"
        note("ok", f"{verb} {len(plan['pod_media'])} POD mockup image(s)")

    note("info", f"metafields → custom.church_store / _name / _url, "
                 f"custom.pod_basis_cost, custom.pod_source_product")
    if args.no_flip:
        note("info", f"publish to {len(cfg['publications'])} channel(s), then "
                     f"{C_YEL}leave the clone DRAFT{C_RESET} (--no-flip)")
    else:
        note("info", f"publish to {len(cfg['publications'])} channel(s), then "
                     f"{C_GRN}clone ACTIVE{C_RESET} / {C_YEL}source DRAFT{C_RESET}")
    print()


def do_clone(shop, cfg, args):
    src = fetch_product(shop, args.ref)
    if args.org not in cfg["orgs"]:
        sys.exit(f"--org must be one of: {', '.join(cfg['orgs'])}")
    plan = build_plan(cfg, src, args.org, args)
    print_plan(src, plan, cfg)

    if not args.commit:
        print(f"{C_YEL}DRY RUN — nothing written. Re-run with --commit.{C_RESET}\n")
        return

    o = plan["org_cfg"]

    # 1. duplicate as DRAFT so nothing is customer-visible mid-transform
    dup = shop.gql("""
      mutation($id: ID!, $t: String!, $img: Boolean!) {
        productDuplicate(productId: $id, newTitle: $t, newStatus: DRAFT,
                         includeImages: $img, synchronous: true) {
          newProduct { id }
          userErrors { field message }
        }
      }""", {"id": src["id"], "t": plan["new_title"],
             "img": bool(args.keep_images)})["productDuplicate"]
    if dup["userErrors"]:
        sys.exit(f"duplicate failed: {dup['userErrors']}")
    new_id = dup["newProduct"]["id"]
    note("ok", f"duplicated → {new_id}")

    # 2. identity + attribution (vendor drives the smart collection)
    shop.mutate("productUpdate", """
      mutation($p: ProductUpdateInput!) {
        productUpdate(product: $p) { product { id } userErrors { field message } } }""",
        {"p": {"id": new_id, "handle": plan["new_handle"], "vendor": o["vendor"],
               "productType": plan["product_type"], "tags": [o["tag"]],
               "templateSuffix": ""}}, True)
    note("ok", f"vendor/type/tags/handle set")

    clone = fetch_product(shop, new_id)

    # 3. drop POD-only options
    for op in clone["options"]:
        if len(op["optionValues"]) == 1 and op["name"] in cfg["drop_single_value_options"]:
            shop.mutate("productOptionsDelete", """
              mutation($p: ID!, $o: [ID!]!) {
                productOptionsDelete(productId: $p, options: $o,
                                     strategy: NON_DESTRUCTIVE) {
                  deletedOptionsIds userErrors { field message } } }""",
                {"p": new_id, "o": [op["id"]]}, True)
            note("ok", f"dropped option {op['name']!r}")

    # 4. re-case option values
    for op in clone["options"]:
        changes = [{"id": ov["id"], "name": titlecase_option(ov["name"])}
                   for ov in op["optionValues"]
                   if titlecase_option(ov["name"]) != ov["name"]]
        if changes:
            shop.mutate("productOptionUpdate", """
              mutation($p: ID!, $o: OptionUpdateInput!, $u: [OptionValueUpdateInput!]) {
                productOptionUpdate(productId: $p, option: $o, optionValuesToUpdate: $u) {
                  userErrors { field message } } }""",
                {"p": new_id, "o": {"id": op["id"]}, "u": changes}, True)
            note("ok", f"re-cased {len(changes)} value(s) on {op['name']!r}")

    clone = fetch_product(shop, new_id)

    # 5. variants: tracking OFF first (Shopify re-assigns locations otherwise),
    #    clear POD SKUs, set in-house cost, carry the real weights over.
    src_by_key = {tuple(sorted((s["name"], s["value"]) for s in v["selectedOptions"]
                               if s["name"] not in
                               [op["name"] for op in plan["drop_options"]])): v
                  for v in src["variants"]}
    updates = []
    for v in clone["variants"]:
        key = tuple(sorted((s["name"], s["value"]) for s in v["selectedOptions"]))
        smatch = src_by_key.get(key)
        inv = {"tracked": False, "requiresShipping": True}
        if plan["house_cost"]:
            inv["cost"] = plan["house_cost"]
        w = (smatch or v)["inventoryItem"]["measurement"]["weight"]
        if w and w.get("value"):
            inv["measurement"] = {"weight": {"value": w["value"], "unit": w["unit"]}}
        upd = {"id": v["id"], "inventoryPolicy": "DENY", "inventoryItem": inv}
        if args.price is not None:
            upd["price"] = money(args.price)
        updates.append(upd)

    for chunk in (updates[i:i + 50] for i in range(0, len(updates), 50)):
        shop.mutate("productVariantsBulkUpdate", """
          mutation($p: ID!, $v: [ProductVariantsBulkInput!]!) {
            productVariantsBulkUpdate(productId: $p, variants: $v) {
              userErrors { field message } } }""",
            {"p": new_id, "v": chunk}, True)
    note("ok", f"{len(updates)} variant(s): tracking off, cost + weight set")

    # 6. inventory locations — detach POD, attach house + org pickup
    clone = fetch_product(shop, new_id)
    detached = attached = 0
    for v in clone["variants"]:
        item = v["inventoryItem"]
        have = {l["location"]["id"]: l["id"] for l in item["inventoryLevels"]}
        for lid, level_id in have.items():
            if lid not in plan["keep_locations"]:
                shop.mutate("inventoryDeactivate", """
                  mutation($l: ID!) { inventoryDeactivate(inventoryLevelId: $l) {
                    userErrors { field message } } }""", {"l": level_id}, True)
                detached += 1
        for lid in plan["keep_locations"]:
            if lid not in have:
                shop.mutate("inventoryActivate", """
                  mutation($i: ID!, $l: ID!) {
                    inventoryActivate(inventoryItemId: $i, locationId: $l) {
                      userErrors { field message } } }""",
                    {"i": item["id"], "l": lid}, True)
                attached += 1
    note("ok", f"inventory: {detached} POD/foreign level(s) detached, {attached} attached")

    # 6b. SKUs, and only now. Shopify refuses to blank the SKU of a variant that
    #     is still stocked at a fulfilment-service location -- "SKU can't be
    #     blank" on every variant. Doing it in step 5 aborted the clone halfway
    #     on 2026-09-17, leaving a half-built duplicate in the store.
    if plan["clear_skus"]:
        sku_updates = [{"id": v["id"], "inventoryItem": {"sku": ""}}
                       for v in clone["variants"]]
        for chunk in (sku_updates[i:i + 50]
                      for i in range(0, len(sku_updates), 50)):
            shop.mutate("productVariantsBulkUpdate", """
              mutation($p: ID!, $v: [ProductVariantsBulkInput!]!) {
                productVariantsBulkUpdate(productId: $p, variants: $v) {
                  userErrors { field message } } }""",
                {"p": new_id, "v": chunk}, True)
        note("ok", f"{len(sku_updates)} POD SKU(s) cleared")

    # 7. metafields — attribution + the pairing/cost data the workbook needs
    mfs = [
        {"ownerId": new_id, "namespace": "custom", "key": "church_store",
         "type": "collection_reference", "value": o["collection_id"]},
        {"ownerId": new_id, "namespace": "custom", "key": "church_store_name",
         "type": "single_line_text_field", "value": o["store_name"]},
        {"ownerId": new_id, "namespace": "custom", "key": "church_store_url",
         "type": "url", "value": cfg["store_url_base"] + o["collection_handle"]},
        {"ownerId": new_id, "namespace": "custom", "key": "pod_source_product",
         "type": "product_reference", "value": src["id"]},
    ]
    if plan["pod_cost"] and not plan["cost_suspect"]:
        mfs.append({"ownerId": new_id, "namespace": "custom", "key": "pod_basis_cost",
                    "type": "number_decimal", "value": plan["pod_cost"]})
    shop.mutate("metafieldsSet", """
      mutation($m: [MetafieldsSetInput!]!) {
        metafieldsSet(metafields: $m) { userErrors { field message } } }""",
        {"m": mfs}, True)
    note("ok", f"{len(mfs)} metafield(s) set")

    # 7b. remove POD-app metafields that rode along on the duplicate
    stale = app_metafields(cfg, fetch_product(shop, new_id))
    if stale:
        shop.mutate("metafieldsDelete", """
          mutation($m: [MetafieldIdentifierInput!]!) {
            metafieldsDelete(metafields: $m) { userErrors { field message } } }""",
            {"m": [{"ownerId": new_id, "namespace": m["namespace"], "key": m["key"]}
                   for m in stale]}, True)
        note("ok", f"stripped {len(stale)} POD-app metafield(s): "
                   + ", ".join(f"{m['namespace']}.{m['key']}" for m in stale))

    # 8. purge any POD mockups that rode along
    leftovers = [m["id"] for m in fetch_product(shop, new_id)["media"]
                 if is_pod_media(cfg, m)]
    if leftovers:
        shop.mutate("productDeleteMedia", """
          mutation($p: ID!, $m: [ID!]!) {
            productDeleteMedia(productId: $p, mediaIds: $m) {
              mediaUserErrors { field message } } }""",
            {"p": new_id, "m": leftovers}, True)
        note("ok", f"removed {len(leftovers)} POD mockup image(s)")

    # 9. publish + flip
    shop.mutate("publishablePublish", """
      mutation($id: ID!, $in: [PublicationInput!]!) {
        publishablePublish(id: $id, input: $in) { userErrors { field message } } }""",
        {"id": new_id, "in": [{"publicationId": p} for p in cfg["publications"]]}, True)
    note("ok", f"published to {len(cfg['publications'])} channel(s)")

    if not args.no_flip:
        shop.mutate("productUpdate", """
          mutation($p: ProductUpdateInput!) {
            productUpdate(product: $p) { userErrors { field message } } }""",
            {"p": {"id": new_id, "status": "ACTIVE"}}, True)
        shop.mutate("productUpdate", """
          mutation($p: ProductUpdateInput!) {
            productUpdate(product: $p) { userErrors { field message } } }""",
            {"p": {"id": src["id"], "status": "DRAFT"}}, True)
        note("ok", "clone ACTIVE, POD source DRAFT")
    else:
        note("warn", "left as DRAFT (--no-flip); run `flip --to twc` when ready")

    print(f"\n{C_GRN}{C_BLD}Done.{C_RESET} {plan['new_title']}")
    print(f"  {new_id}   ({shop.calls} API calls)")
    if not plan["house_cost"]:
        print(f"  {C_YEL}Next:{C_RESET} set the real in-house cost — "
              f"re-run with --cost, or edit Cost per item in admin.")
    print(f"  {C_YEL}Next:{C_RESET} swap in your own mockups; POD photos are not on this listing.\n")


# ─────────────────────────────────────────────────────────────── flip ──

def find_twin(shop, cfg, product):
    """Twin by metafield link first, then by title convention."""
    for mf in product["metafields"]:
        if mf["namespace"] == "custom" and mf["key"] == "pod_source_product":
            return mf["value"], "metafield"
    # Reverse direction: we were handed the POD half. Scan the in-house listings
    # for the one whose pod_source_product points back at us.
    d = shop.gql("""query($q: String!) {
        products(first: 250, query: $q) {
          edges { node { id title
            metafield(namespace: "custom", key: "pod_source_product") { value } } } } }""",
        {"q": f'title:*{cfg["twc_suffix"].strip()}'})
    for e in d["products"]["edges"]:
        m = e["node"].get("metafield")
        if m and m["value"] == product["id"]:
            return e["node"]["id"], "metafield"
    base = strip_pod_noise(product["title"]).removesuffix(cfg["twc_suffix"])
    d = shop.gql('query($q: String!) { products(first: 20, query: $q) '
                 '{ edges { node { id title } } } }', {"q": f'title:{base}*'})
    for e in d["products"]["edges"]:
        n = e["node"]
        if n["id"] != product["id"] and n["title"].endswith(cfg["twc_suffix"]):
            return n["id"], "title"
    return None, None


def do_flip(shop, cfg, args):
    p = fetch_product(shop, args.ref)
    twin_id, how = find_twin(shop, cfg, p)
    if not twin_id:
        sys.exit(f"No twin found for {p['title']!r}. Clone it first.")
    twin = fetch_product(shop, twin_id)
    is_twc = p["title"].endswith(cfg["twc_suffix"])
    twc, pod = (p, twin) if is_twc else (twin, p)
    winner, loser = (twc, pod) if args.to == "twc" else (pod, twc)
    print(f"\n  ACTIVE → {C_GRN}{winner['title']}{C_RESET}   {C_DIM}({winner['status']}){C_RESET}")
    print(f"  DRAFT  → {C_YEL}{loser['title']}{C_RESET}   {C_DIM}({loser['status']}){C_RESET}")
    print(f"  {C_DIM}paired by {how}{C_RESET}\n")

    # Refuse to flip onto a half that offers less than the one going dark.
    info = option_values(shop, [twc["id"], pod["id"]])
    a, b = info.get(twc["id"]), info.get(pod["id"])
    losing = []
    if a and b:
        want = "missing_on_pod" if args.to == "pod" else "missing_on_twc"
        losing = [g for g in coverage_gaps(cfg, a, b) if g[0] == want]
    if a and a.get("accepted"):
        note("dim", f"accepted coverage exceptions: {', '.join(a['accepted'])}")
    if losing:
        note("err", f"{winner['title'][:50]} does not cover everything "
                    f"{loser['title'][:50]} does:")
        for _, opt, vals in losing:
            note("err", f"    {opt}: {', '.join(vals[:8])}"
                        + ("…" if len(vals) > 8 else ""))
        note("warn", "flipping would remove those from the storefront")
        if not args.force:
            print(f"\n{C_RED}Refusing to flip.{C_RESET} Even up the halves first, "
                  f"or re-run with --force if you mean it.\n")
            return

    if not args.commit:
        print(f"{C_YEL}DRY RUN — re-run with --commit.{C_RESET}\n")
        return
    for prod, status in ((winner, "ACTIVE"), (loser, "DRAFT")):
        shop.mutate("productUpdate", """
          mutation($p: ProductUpdateInput!) {
            productUpdate(product: $p) { userErrors { field message } } }""",
            {"p": {"id": prod["id"], "status": status}}, True)
    note("ok", "flipped")


# ─────────────────────────────────────────────────────────────── audit ──

def all_products(shop):
    out, after = [], None
    while True:
        d = shop.gql("""query($a: String) {
            products(first: 100, after: $a) {
              edges { node { id title status vendor
                variants(first: 1) { edges { node { price inventoryItem {
                  unitCost { amount } tracked requiresShipping
                  measurement { weight { value unit } }
                  inventoryLevels(first: 10) { edges { node { location { id name } } } } } } } }
                metafields(first: 20) { edges { node { namespace key value } } } } }
              pageInfo { hasNextPage endCursor } } }""", {"a": after})
        for e in d["products"]["edges"]:
            out.append(e["node"])
        if not d["products"]["pageInfo"]["hasNextPage"]:
            return out
        after = d["products"]["pageInfo"]["endCursor"]


def option_values(shop, ids):
    """{product_id: {title, count, options, variants}} for each half.

    Both the declared option values AND the actual variant combinations are
    needed. Declared options alone are not enough: a product can list
    Color [Black, White] and Size [S..4XL] while the grid is missing
    White/S and White/4XL — every option value present, two variants absent.
    """
    out = {}
    ids = list(ids)
    for i in range(0, len(ids), 25):
        d = shop.gql("""query($ids: [ID!]!) {
            nodes(ids: $ids) { ... on Product {
              id title
              variantsCount { count }
              options { name optionValues { name } }
              exceptions: metafield(namespace: "custom",
                                    key: "pair_coverage_exceptions") { value }
              variants(first: 100) { edges { node {
                selectedOptions { name value } } } } } } }""",
            {"ids": ids[i:i + 25]})
        for n in d["nodes"]:
            if not n:
                continue
            raw = (n.get("exceptions") or {}).get("value")
            try:
                accepted = json.loads(raw) if raw else []
            except json.JSONDecodeError:
                accepted = [raw] if raw else []
            out[n["id"]] = {
                "title": n["title"],
                "count": n["variantsCount"]["count"],
                "options": {o["name"]: [v["name"] for v in o["optionValues"]]
                            for o in n["options"]},
                "variants": [{s["name"]: s["value"] for s in e["node"]["selectedOptions"]}
                             for e in n["variants"]["edges"]],
                "accepted": [str(a) for a in accepted],
            }
    return out


def _label_norm(s):
    """'White / S' and 'white/s' compare equal."""
    return " / ".join(p.strip().lower() for p in str(s).split("/"))


def _keys(variants, ignore):
    """{comparable key: display label}. The key is case-normalised with POD
    packaging options dropped; the label keeps that side's own spelling."""
    out = {}
    for sel in variants:
        items = sorted((n.lower(), v) for n, v in sel.items()
                       if n.lower() not in ignore)
        key = tuple((n, titlecase_option(v).strip().lower()) for n, v in items)
        out[key] = " / ".join(v for _, v in items)
    return out


def coverage_gaps(cfg, twc, pod, accepted=None):
    """Everything one half sells that the other doesn't.

    Compares actual variant combinations, not just declared option values, so
    holes in the grid are caught. Options that exist on only one side and carry
    a single value are dropped from the comparison first — that is Ninja POD's
    "Product" option holding the blank garment name, which is packaging, not
    choice. A one-sided option with real choice in it is reported instead.

    `accepted` is the pair's recorded coverage exceptions — combinations the
    supplier genuinely can't make, which you've decided to live with. Those are
    excluded so the check keeps firing on anything NEW. Defaults to whatever
    custom.pair_coverage_exceptions holds on the in-house half.
    """
    if accepted is None:
        accepted = twc.get("accepted", [])
    ok = {_label_norm(a) for a in accepted}
    gaps = []
    tnames = {n.lower(): (n, v) for n, v in twc["options"].items()}
    pnames = {n.lower(): (n, v) for n, v in pod["options"].items()}

    ignore = set()
    for lower, (name, vals) in list(tnames.items()) + list(pnames.items()):
        on_both = lower in tnames and lower in pnames
        if on_both:
            continue
        if len(vals) <= 1:
            ignore.add(lower)
        else:
            side = "twc_only_option" if lower in tnames else "pod_only_option"
            if (side, name) not in {(g[0], g[1]) for g in gaps}:
                gaps.append((side, name, vals))

    tmap = _keys(twc["variants"], ignore)
    pmap = _keys(pod["variants"], ignore)
    tkeys, pkeys = set(tmap), set(pmap)

    for kind, missing, source in (("missing_on_pod", tkeys - pkeys, tmap),
                                  ("missing_on_twc", pkeys - tkeys, pmap)):
        # Drop recorded exceptions before anything else, so an accepted hole
        # doesn't stop a whole-colour gap collapsing onto one line either.
        missing = {k for k in missing if _label_norm(source[k]) not in ok}
        source = {k: v for k, v in source.items() if _label_norm(v) not in ok}
        if not missing:
            continue
        # Collapse "every combination using this value is absent" into one line.
        whole = {}
        for optname, val in {kv for k in missing for kv in k}:
            using = {k for k in source if (optname, val) in k}
            if len(using) > 1 and using <= missing:
                whole[(optname, val)] = using
        covered = set().union(*whole.values()) if whole else set()
        for (optname, val), using in sorted(whole.items()):
            label = next((n for n in (list(twc["options"]) + list(pod["options"]))
                          if n.lower() == optname), optname)
            shown = next((dict(zip((v for _, v in k), source[k].split(" / ")))
                          for k in using), {}).get(val, val)
            gaps.append((kind, label, [f"{shown} (all {len(using)})"]))
        rest = sorted(source[k] for k in missing - covered)
        if rest:
            gaps.append((kind, "combination", rest))
    return gaps


def do_audit(shop, cfg, args):
    prods = all_products(shop)
    sfx = cfg["twc_suffix"]
    twc = [p for p in prods if p["title"].endswith(sfx)]
    pod_locs = set(cfg["pod_locations"])

    def mf(p, key):
        for e in p["metafields"]["edges"]:
            if e["node"]["key"] == key:
                return e["node"]["value"]
        return None

    print(f"\n{C_BLD}{len(prods)} products · {len(twc)} in-house (TWC){C_RESET}\n")
    problems = []
    for p in sorted(twc, key=lambda x: x["title"]):
        issues = []
        if not mf(p, "pod_source_product"):
            issues.append("no POD source link")
        if not mf(p, "pod_basis_cost"):
            issues.append("no POD basis cost")
        if not mf(p, "church_store"):
            issues.append("no church_store metafield")
        v = (p["variants"]["edges"] or [{}])[0].get("node")
        if v:
            item = v["inventoryItem"]
            if not item.get("unitCost"):
                issues.append("no actual cost")
            elif item["unitCost"]["amount"] == v.get("price"):
                issues.append(f"cost == retail ({v['price']}) — placeholder, not a cost")
            if item.get("tracked"):
                issues.append("inventory still TRACKED (untracked is how these sell)")
            w = (item.get("measurement") or {}).get("weight") or {}
            if item.get("requiresShipping") and not w.get("value"):
                issues.append("weight 0 but requires shipping — shipping rates will be off")
            for l in item["inventoryLevels"]["edges"]:
                if l["node"]["location"]["id"] in pod_locs:
                    issues.append(f"STILL STOCKED AT {l['node']['location']['name']}")
        for e in p["metafields"]["edges"]:
            if e["node"]["namespace"] in cfg.get("app_metafield_namespaces", []):
                issues.append(f"POD app metafield {e['node']['namespace']}."
                              f"{e['node']['key']} still attached")
        if p["vendor"] in cfg["pod_vendors"]:
            issues.append(f"vendor still {p['vendor']!r}")
        if issues:
            problems.append((p, issues))
            print(f"  {C_RED}✗{C_RESET} {p['title']}")
            for i in issues:
                print(f"      {C_YEL}{i}{C_RESET}")

    # Pair coverage. A dual-SKU pair is only safe to flip if both halves offer
    # the same colours and sizes — otherwise flipping silently drops variants a
    # customer could buy yesterday.
    pairs = [(p, mf(p, "pod_source_product")) for p in twc
             if mf(p, "pod_source_product")]
    mismatched = []
    if pairs:
        info = option_values(shop, {i for p in pairs for i in (p[0]["id"], p[1])})
        for t, pod_id in pairs:
            a, b = info.get(t["id"]), info.get(pod_id)
            if not a or not b:
                continue
            gaps = coverage_gaps(cfg, a, b)
            if gaps:
                mismatched.append((a, b, gaps))

        if mismatched:
            print(f"\n{C_BLD}Pair coverage mismatches{C_RESET} "
                  f"{C_DIM}(halves are not interchangeable — flipping drops "
                  f"variants){C_RESET}")
            label = {
                "missing_on_pod": ("POD half is missing", C_RED),
                "missing_on_twc": ("in-house half is missing", C_RED),
                "pod_only_option": ("option only on the POD half", C_YEL),
                "twc_only_option": ("option only on the in-house half", C_YEL),
            }
            for a, b, gaps in sorted(mismatched, key=lambda x: x[0]["title"]):
                print(f"\n  {C_RED}✗{C_RESET} {a['title'][:64]}")
                print(f"      {C_DIM}in-house {a['count']} variants  ·  "
                      f"POD {b['count']} variants{C_RESET}")
                if a.get("accepted"):
                    print(f"      {C_DIM}accepted: "
                          f"{', '.join(a['accepted'])}{C_RESET}")
                for kind, opt, vals in gaps:
                    text, colour = label[kind]
                    shown = ", ".join(vals[:8]) + ("…" if len(vals) > 8 else "")
                    print(f"      {colour}{text} {opt}: {shown}{C_RESET}")

    linked = {e["node"]["value"] for p in prods for e in p["metafields"]["edges"]
              if e["node"]["key"] == "pod_source_product"}
    orphan_pod = [p for p in prods
                  if not p["title"].endswith(sfx)
                  and p["id"] not in linked
                  and pod_side(cfg, p)]
    if orphan_pod:
        print(f"\n{C_BLD}POD-side products with no in-house twin{C_RESET}")
        for p in sorted(orphan_pod, key=lambda x: x["title"]):
            print(f"  {C_CYN}·{C_RESET} {p['title'][:56]:<56} {C_DIM}{p['status']:<8} "
                  f"{pod_side(cfg, p)}  {p['id'].rsplit('/',1)[-1]}{C_RESET}")

    print(f"\n{len(problems)} in-house listing(s) with gaps, "
          f"{len(mismatched)} pair(s) not safe to flip, "
          f"{len(orphan_pod)} un-cloned POD listing(s).\n")


def pod_side(cfg, p):
    """Is this the POD half? Vendor is not enough — Printify listings often already
    carry the org vendor. Stock at a POD fulfillment location is the real tell."""
    if p["vendor"] in cfg["pod_vendors"]:
        return cfg["pod_vendors"][0] if isinstance(cfg["pod_vendors"], list) else True
    for e in p["variants"]["edges"]:
        for l in e["node"]["inventoryItem"]["inventoryLevels"]["edges"]:
            if l["node"]["location"]["id"] in cfg["pod_locations"]:
                return l["node"]["location"]["name"]
    for e in p["metafields"]["edges"]:
        if e["node"]["namespace"] in cfg.get("app_metafield_namespaces", []):
            return e["node"]["namespace"]
    return None


def do_list(shop, cfg, args):
    prods = all_products(shop)
    sfx = cfg["twc_suffix"]
    twins = {e["node"]["value"] for p in prods for e in p["metafields"]["edges"]
             if e["node"]["key"] == "pod_source_product"}
    print(f"\n{C_BLD}POD-side listings with no in-house twin{C_RESET}\n")
    n = 0
    for p in sorted(prods, key=lambda x: x["title"]):
        if p["title"].endswith(sfx) or p["id"] in twins:
            continue
        why = pod_side(cfg, p)
        if not why:
            continue
        n += 1
        cost = (p["variants"]["edges"] or [{}])[0].get("node", {}) \
                .get("inventoryItem", {}).get("unitCost")
        c = f"cost {cost['amount']}" if cost else f"{C_RED}no cost{C_RESET}"
        print(f"  {p['id'].rsplit('/',1)[-1]}  {p['title'][:48]:<48} "
              f"{p['status']:<8} {str(why)[:14]:<14} {c}")
    print(f"\n  {n} candidate(s)\n")


def do_except(shop, cfg, args):
    """Record combinations the POD supplier can't make, so the flip guard stops
    reporting them without going quiet about anything new."""
    p = fetch_product(shop, args.ref)
    twin_id, how = find_twin(shop, cfg, p)
    if not twin_id:
        sys.exit(f"No twin found for {p['title']!r}.")
    twin = fetch_product(shop, twin_id)
    is_twc = p["title"].endswith(cfg["twc_suffix"])
    twc, pod = (p, twin) if is_twc else (twin, p)

    info = option_values(shop, [twc["id"], pod["id"]])
    a, b = info[twc["id"]], info[pod["id"]]
    current = list(a.get("accepted", []))

    print(f"\n{C_BLD}{a['title']}{C_RESET}")
    print(f"  {C_DIM}in-house {a['count']} variants  ·  POD {b['count']} variants  "
          f"· paired by {how}{C_RESET}\n")

    # Every gap, ignoring exceptions — so we can show which are covered.
    raw = coverage_gaps(cfg, a, b, accepted=[])
    open_combos = [v for k, o, vals in raw if o == "combination"
                   and k == "missing_on_pod" for v in vals]
    known = {_label_norm(x) for x in current}

    print(f"  {C_BLD}recorded exceptions{C_RESET}")
    if current:
        for x in current:
            live = "" if _label_norm(x) in {_label_norm(c) for c in open_combos} \
                   else f"  {C_YEL}(no longer a gap — safe to remove){C_RESET}"
            print(f"    {C_GRN}✓{C_RESET} {x}{live}")
    else:
        print(f"    {C_DIM}none{C_RESET}")

    unlisted = [c for c in open_combos if _label_norm(c) not in known]
    print(f"\n  {C_BLD}gaps not yet accepted{C_RESET}")
    if unlisted:
        for c in unlisted:
            print(f"    {C_RED}✗{C_RESET} {c}")
    else:
        print(f"    {C_DIM}none{C_RESET}")

    new = list(current)
    for add in args.add or []:
        if _label_norm(add) in {_label_norm(x) for x in new}:
            note("dim", f"already recorded: {add}")
            continue
        if _label_norm(add) not in {_label_norm(c) for c in open_combos}:
            note("warn", f"{add!r} is not a current gap — check the spelling "
                         f"(expected one of: {', '.join(open_combos) or 'none'})")
        new.append(add)
    for rm in args.remove or []:
        before = len(new)
        new = [x for x in new if _label_norm(x) != _label_norm(rm)]
        if len(new) == before:
            note("warn", f"{rm!r} was not recorded")

    if new == current:
        print()
        return
    print(f"\n  {C_BLD}→ {len(new)} exception(s):{C_RESET} "
          f"{', '.join(new) or '(cleared)'}\n")
    if not args.commit:
        print(f"{C_YEL}DRY RUN — re-run with --commit.{C_RESET}\n")
        return
    shop.mutate("metafieldsSet", """
      mutation($m: [MetafieldsSetInput!]!) {
        metafieldsSet(metafields: $m) { userErrors { field message } } }""",
        {"m": [{"ownerId": twc["id"], "namespace": "custom",
                "key": "pair_coverage_exceptions",
                "type": "list.single_line_text_field",
                "value": json.dumps(new)}]}, True)
    note("ok", f"recorded on {a['title']}")


def do_metafields(shop, cfg, args):
    """Recompute church_store / _name / _url from vendor and repair drift.

    Vendor is the single source of truth in this store — the storefront collections
    are `VENDOR EQUALS` smart rules, so the attribution metafields are a pure
    function of vendor. Anything that disagrees with that function is wrong,
    whatever wrote it. This makes Shopify Flow non-load-bearing for attribution:
    if Flow misfires, this repairs it.
    """
    by_vendor = {o["vendor"]: o for o in cfg["orgs"].values()}
    prods = all_products(shop)

    def mf(p, key):
        for e in p["metafields"]["edges"]:
            if e["node"]["key"] == key:
                return e["node"]["value"]
        return None

    drift, unknown, writes = [], [], []
    for p in sorted(prods, key=lambda x: x["title"]):
        org = by_vendor.get(p["vendor"])
        if not org:
            unknown.append(p)
            continue
        want = {
            "church_store": org["collection_id"],
            "church_store_url": cfg["store_url_base"] + org["collection_handle"],
        }
        if args.fix_names:
            want["church_store_name"] = org["store_name"]
        # Untouched products (POD source shells) have no attribution at all.
        # Don't invent it unless asked — only repair what's there and wrong.
        if not args.fill and not any(mf(p, k) for k in
                                     ("church_store", "church_store_name",
                                      "church_store_url")):
            continue
        bad = {k: (mf(p, k), v) for k, v in want.items() if mf(p, k) != v}
        if not bad:
            continue
        drift.append((p, bad))
        types = {"church_store": "collection_reference",
                 "church_store_url": "url",
                 "church_store_name": "single_line_text_field"}
        for k, (_, v) in bad.items():
            writes.append({"ownerId": p["id"], "namespace": "custom",
                           "key": k, "type": types[k], "value": v})

    print(f"\n{C_BLD}Attribution drift{C_RESET} {C_DIM}(expected values derived "
          f"from vendor){C_RESET}\n")
    for p, bad in drift:
        print(f"  {C_RED}✗{C_RESET} {p['title'][:60]}")
        print(f"      {C_DIM}{p['status']}  vendor={p['vendor']!r}{C_RESET}")
        for k, (was, now) in bad.items():
            print(f"      {k}")
            print(f"        was  {C_RED}{was}{C_RESET}")
            print(f"        →    {C_GRN}{now}{C_RESET}")

    if unknown:
        print(f"\n{C_BLD}Vendor not in the org registry{C_RESET} "
              f"{C_DIM}(cannot be attributed — fix the vendor first){C_RESET}")
        for p in unknown:
            print(f"  {C_YEL}!{C_RESET} {p['title'][:56]:<56} "
                  f"{p['status']:<8} vendor={p['vendor']!r}")

    print(f"\n  {len(drift)} product(s) drifted, {len(writes)} metafield write(s), "
          f"{len(unknown)} unattributable\n")
    if not args.fix_names:
        print(f"  {C_DIM}church_store_name left alone — add --fix-names to reset it "
              f"to the registry value too.{C_RESET}\n")

    if not writes:
        return
    if not args.commit:
        print(f"{C_YEL}DRY RUN — re-run with --commit.{C_RESET}\n")
        return
    for chunk in (writes[i:i + 25] for i in range(0, len(writes), 25)):
        shop.mutate("metafieldsSet", """
          mutation($m: [MetafieldsSetInput!]!) {
            metafieldsSet(metafields: $m) { userErrors { field message } } }""",
            {"m": chunk}, True)
    note("ok", f"{len(writes)} metafield(s) repaired across {len(drift)} product(s)")


def do_show(shop, cfg, args):
    p = fetch_product(shop, args.ref)
    print(json.dumps({
        "id": p["id"], "title": p["title"], "status": p["status"],
        "vendor": p["vendor"], "type": p["productType"], "tags": p["tags"],
        "options": [{"name": o["name"], "values": [v["name"] for v in o["optionValues"]]}
                    for o in p["options"]],
        "variants": len(p["variants"]),
        "source_app": detect_app(cfg, p),
        "pod_cost": pod_cost_of(p)[0],
        "pod_cost_is_placeholder": pod_cost_of(p)[1],
        "tracked_variants": sum(1 for v in p["variants"]
                                if v["inventoryItem"].get("tracked")),
        "app_metafields": [f"{m['namespace']}.{m['key']}" for m in app_metafields(cfg, p)],
        "locations": sorted({l["location"]["name"] for v in p["variants"]
                             for l in v["inventoryItem"]["inventoryLevels"]}),
        "metafields": {f"{m['namespace']}.{m['key']}": m["value"] for m in p["metafields"]},
        "pod_media": sum(1 for m in p["media"] if is_pod_media(cfg, m)),
    }, indent=2))


def do_scrub(shop, cfg, args):
    """Re-run the strip pass on an existing in-house listing."""
    args_ns = argparse.Namespace(**vars(args))
    p = fetch_product(shop, args.ref)
    org_cfg = cfg["orgs"][args.org]
    keep = set(org_cfg["pickup_locations"]) | {cfg["house_location"]}
    print(f"\n{C_BLD}SCRUB{C_RESET} {p['title']}\n")
    fixes = 0
    for v in p["variants"]:
        for l in v["inventoryItem"]["inventoryLevels"]:
            if l["location"]["id"] not in keep:
                fixes += 1
                if args.commit:
                    shop.mutate("inventoryDeactivate", """
                      mutation($l: ID!) { inventoryDeactivate(inventoryLevelId: $l) {
                        userErrors { field message } } }""", {"l": l["id"]}, True)
    note("ok" if args.commit else "info",
         f"{fixes} foreign inventory level(s) " + ("detached" if args.commit else "would detach"))
    if p["vendor"] != org_cfg["vendor"]:
        note("warn", f"vendor {p['vendor']!r} → {org_cfg['vendor']!r}")
        if args.commit:
            shop.mutate("productUpdate", """
              mutation($p: ProductUpdateInput!) {
                productUpdate(product: $p) { userErrors { field message } } }""",
                {"p": {"id": p["id"], "vendor": org_cfg["vendor"],
                       "tags": [org_cfg["tag"]]}}, True)
    if not args.commit:
        print(f"\n{C_YEL}DRY RUN — re-run with --commit.{C_RESET}\n")


# ─────────────────────────────────────────────────────────────── cli ──

def main():
    cfg = json.loads(CONFIG_PATH.read_text())
    ap = argparse.ArgumentParser(prog="pod2twc", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--commit", action="store_true",
                       help="actually write (default is a dry run)")

    p = sub.add_parser("clone", help="build the in-house twin of a POD listing")
    p.add_argument("ref")
    p.add_argument("--org", required=True, choices=sorted(cfg["orgs"]))
    p.add_argument("--title", help="override the base title (' TWC' is appended)")
    p.add_argument("--cost", type=float, help="in-house unit cost per variant")
    p.add_argument("--pod-cost", dest="pod_cost", type=float,
                   help="true POD cost for the payout basis. Needed when the app pushed "
                        "cost = retail (Printify does)")
    p.add_argument("--price", type=float,
                   help="override retail on every variant (default: keep the POD price, "
                        "since retail must match across the pair)")
    p.add_argument("--product-type", dest="product_type")
    p.add_argument("--keep-images", action="store_true",
                   help="copy POD mockups too (default: start with no images)")
    p.add_argument("--keep-skus", action="store_true")
    p.add_argument("--no-flip", action="store_true",
                   help="leave the clone DRAFT and the POD listing as-is")
    common(p)

    p = sub.add_parser("flip", help="switch which half of a pair is live")
    p.add_argument("ref")
    p.add_argument("--to", required=True, choices=["twc", "pod"])
    p.add_argument("--force", action="store_true",
                   help="flip even if the half going live covers fewer "
                        "colours/sizes than the one going dark")
    common(p)

    p = sub.add_parser("audit", help="all pairs + missing cost/link/location data")
    common(p)

    p = sub.add_parser("list", help="POD listings with no in-house twin")
    common(p)

    p = sub.add_parser("except",
                       help="record coverage gaps the POD supplier can't fill")
    p.add_argument("ref")
    p.add_argument("--add", action="append", metavar='"White / S"',
                   help="accept this combination as permanently unavailable")
    p.add_argument("--remove", action="append", metavar='"White / S"')
    common(p)

    p = sub.add_parser("metafields",
                       help="recompute church_store/_name/_url from vendor, repair drift")
    p.add_argument("--fix-names", action="store_true",
                   help="also reset church_store_name to the registry value")
    p.add_argument("--fill", action="store_true",
                   help="also attribute products that have no attribution at all "
                        "(POD source shells) instead of skipping them")
    common(p)

    p = sub.add_parser("show", help="dump one product as the script sees it")
    p.add_argument("ref")
    common(p)

    p = sub.add_parser("scrub", help="re-strip an existing in-house listing")
    p.add_argument("ref")
    p.add_argument("--org", required=True, choices=sorted(cfg["orgs"]))
    common(p)

    args = ap.parse_args()
    shop = Shop(cfg)
    {"clone": do_clone, "flip": do_flip, "audit": do_audit, "list": do_list,
     "show": do_show, "scrub": do_scrub, "metafields": do_metafields,
     "except": do_except}[args.cmd](shop, cfg, args)


if __name__ == "__main__":
    main()
