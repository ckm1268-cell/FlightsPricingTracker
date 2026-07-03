#!/usr/bin/env python3
"""
Parses a GitHub Issue created by the Mini App's "Remove" button (labeled
"remove-route") and removes the matching route (by origin+destination)
from config.yaml.

Reads the issue body from the ISSUE_BODY environment variable. Expected
format (mirrors the add-route issue format):

    ### Origin

    NYC

    ### Destination

    TYO
"""

import os
import re
import sys
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


def parse_all_fields(body):
    chunks = re.split(r"\n(?=### )", body.strip())
    fields = {}
    for chunk in chunks:
        if not chunk.startswith("### "):
            continue
        label_line, _, rest = chunk[len("### "):].partition("\n")
        value = rest.strip()
        if value.lower() == "_no response_":
            value = ""
        fields[label_line.strip()] = value
    return fields


def main():
    body = os.environ.get("ISSUE_BODY", "")
    if not body:
        print("ERROR: ISSUE_BODY is empty.", file=sys.stderr)
        sys.exit(1)

    fields = parse_all_fields(body)
    origin = fields.get("Origin", "").strip().upper()
    destination = fields.get("Destination", "").strip().upper()

    if not origin or not destination:
        print("ERROR: missing origin or destination in issue body.", file=sys.stderr)
        sys.exit(1)

    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f) or {"routes": []}
    routes = config.get("routes") or []

    remaining = [r for r in routes if not (r.get("origin") == origin and r.get("destination") == destination)]
    removed = len(remaining) != len(routes)

    config["routes"] = remaining
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    if removed:
        confirmation = f"Route removed: {origin} -> {destination}. Now tracking {len(remaining)} route(s)."
    else:
        confirmation = f"No matching route found for {origin} -> {destination} (nothing removed). Currently tracking {len(remaining)} route(s)."

    print(confirmation)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write(confirmation + "\n")

    out_path = Path(os.environ.get("GITHUB_WORKSPACE", ".")) / "route_confirmation.txt"
    out_path.write_text(confirmation)


if __name__ == "__main__":
    main()
