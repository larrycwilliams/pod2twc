#!/usr/bin/env python3
"""
Generate one Shopify Flow workflow per partner organisation.

Replaces the single "Set church store metafields v2" workflow, whose False path
fans into unconditional metafield actions for every church (last write wins —
Community Christian was winning). One workflow per vendor makes that class of
bug structurally impossible: there is no shared branch to get wrong, and adding
a church is "copy a file", not "operate on a canvas".

Each generated workflow:

    Product created
      └─ Condition: product.vendor == "<vendor>"
           └─ true → set custom.church_store       (collection_reference)
                   → set custom.church_store_name  (single line text)
                   → set custom.church_store_url   (url)

Actions are chained through each action's `output` port, so they run in a
defined order rather than racing.

Usage:  python3 make_flows.py [outdir]
Reads the org registry from config.json — the same one pod2twc.py uses.
"""

import hashlib
import json
import os
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / "config.json").read_text())

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid():
    """Crockford base32 ULID: 10 chars of ms timestamp + 16 random."""
    ms = int(time.time() * 1000)
    out = ""
    for _ in range(10):
        out = CROCKFORD[ms % 32] + out
        ms //= 32
    return out + "".join(random.choice(CROCKFORD) for _ in range(16))


def action(step_id, x, y, namespace, key, mtype, value):
    return {
        "step_id": step_id,
        "step_position": [x, y],
        "config_field_values": [
            {"config_field_id": "product_id",
             "value": json.dumps({"value": "", "default_value": "product.id"})},
            {"config_field_id": "metafield",
             "value": json.dumps({"namespace": namespace, "key": key, "type": mtype})},
            {"config_field_id": "value", "value": value},
        ],
        "task_id": "shopify::admin::add_product_metafield",
        "task_version": "1.0",
        "task_type": "ACTION",
        "description": None, "note": None, "name": None,
    }


def vendor_condition(step_id, x, y, vendor):
    outer, inner, lhs, rhs = ulid(), ulid(), ulid(), ulid()
    cond = {
        "uuid": outer,
        "lhs": {
            "uuid": inner, "parent_uuid": outer,
            "lhs": {"uuid": lhs, "parent_uuid": inner, "value": "product.vendor",
                    "comparison_value_type": "EnvironmentValue",
                    "full_environment_path": "product.vendor"},
            "rhs": {"uuid": rhs, "parent_uuid": inner, "value": vendor,
                    "comparison_value_type": "LiteralValue"},
            "value_type": "EnvironmentScalarDefinition:String",
            "operator": "==", "operation_type": "Comparison",
        },
        "operator": "AND", "operation_type": "LogicalExpression",
    }
    return {
        "step_id": step_id,
        "step_position": [x, y],
        "config_field_values": [
            {"config_field_id": "condition", "value": json.dumps(cond)}],
        "task_id": "shopify::flow::condition",
        "task_version": "0.1",
        "task_type": "CONDITION",
        "description": None, "note": None, "name": None,
    }


def build(name, vendor, collection_id, store_name, url):
    trig, cond = ulid(), ulid()
    a1, a2, a3 = ulid(), ulid(), ulid()
    steps = [
        {"step_id": trig, "step_position": [0, 0], "config_field_values": [],
         "task_id": "shopify::admin::product_added", "task_version": "0.1",
         "task_type": "TRIGGER", "description": None, "note": None, "name": None},
        vendor_condition(cond, 0, 200, vendor),
        action(a1, 0, 380, "custom", "church_store", "collection_reference",
               collection_id),
        action(a2, 0, 520, "custom", "church_store_name",
               "single_line_text_field", store_name),
        action(a3, 0, 660, "custom", "church_store_url", "url", url),
    ]
    links = [
        {"from_step_id": trig, "from_port_id": "output",
         "to_step_id": cond, "to_port_id": "input"},
        {"from_step_id": cond, "from_port_id": "true",
         "to_step_id": a1, "to_port_id": "input"},
        {"from_step_id": a1, "from_port_id": "output",
         "to_step_id": a2, "to_port_id": "input"},
        {"from_step_id": a2, "from_port_id": "output",
         "to_step_id": a3, "to_port_id": "input"},
    ]
    doc = {"__metadata": {"version": 0.1},
           "root": {"steps": steps, "links": links, "patched_fields": [],
                    "variables": [], "note": None,
                    "vertical_layout_enabled": True, "workflow_name": name}}
    body = json.dumps(doc, separators=(",", ":"))
    # Shopify prefixes the file with a digest we cannot reproduce (HMAC with a
    # server-side secret). sha256 of the body is the best-effort placeholder;
    # if import rejects it, the file itself is still correct — rebuild in the UI.
    return hashlib.sha256(body.encode()).hexdigest() + ":" + body


def main():
    outdir = Path(sys.argv[1] if len(sys.argv) > 1 else HERE / "flows")
    outdir.mkdir(parents=True, exist_ok=True)
    # Steeple & Stitch Co. is the house brand, not a partner org — the current
    # workflow deliberately excludes it and its products carry a hand-set
    # church_store_name ("The Williams Collective"). Leave it out.
    skip = {"steeple-stitch"}
    made = []
    for key, o in sorted(CFG["orgs"].items()):
        if key in skip:
            continue
        name = f"Church metafields — {o['store_name']}"
        content = build(name, o["vendor"], o["collection_id"], o["store_name"],
                        CFG["store_url_base"] + o["collection_handle"])
        path = outdir / f"{key}.flow"
        path.write_text(content)
        made.append((key, o["vendor"], path))
    w = max(len(k) for k, _, _ in made)
    for key, vendor, path in made:
        print(f"  {key:<{w}}  vendor={vendor!r}")
    print(f"\n{len(made)} workflow file(s) in {outdir}")
    print(f"skipped: {', '.join(sorted(skip))} (house brand, excluded by design)")


if __name__ == "__main__":
    main()
