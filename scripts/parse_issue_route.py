#!/usr/bin/env python3
"""
Parses a GitHub Issue Form submission (the "Add a flight route to track"
form) and REPLACES the tracked route in config.yaml with this new one.
Only one route is tracked at a time - each new submission overwrites the
previous route entirely.

Reads the issue body from the ISSUE_BODY environment variable. GitHub
renders issue forms as markdown like:

    ### Route name

    New York to Tokyo

    ### Departure city (IATA code)

    NYC

    ...

This script extracts each field by matching on the form's question labels.
"""

import os
import re
import sys
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"

FIELDS = {
    "route_name": "Route name",
    "origin": "Departure city (IATA code)",
    "destination": "Destination city (IATA code)",
    "departure_date": "Departure date",
    "return_date": "Return date (leave blank for one-way)",
    "target_price": "Target price",
    "currency": "Currency",
}


def parse_all_fields(body):
    """
    Splits the issue body into {label: value} pairs. Splits the text right
    before every '### ' heading, then for each chunk takes the first line
    as the label and everything after it as the value. This avoids regex
    lookahead edge cases that misfire when an optional field is left
    genuinely blank (as opposed to GitHub's usual '_No response_' text).
    """
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


def extract_field(fields, label):
    return fields.get(label, "")


def main():
    body = os.environ.get("ISSUE_BODY", "")
    if not body:
        print("ERROR: ISSUE_BODY is empty.", file=sys.stderr)
        sys.exit(1)

    parsed_fields = parse_all_fields(body)
    parsed = {key: extract_field(parsed_fields, label) for key, label in FIELDS.items()}

    # Validate required fields
    required = ["route_name", "origin", "destination", "departure_date", "target_price", "currency"]
    missing = [r for r in required if not parsed[r]]
    if missing:
        print(f"ERROR: missing required fields: {missing}", file=sys.stderr)
        sys.exit(1)

    try:
        target_price = float(parsed["target_price"])
    except ValueError:
        print(f"ERROR: target_price '{parsed['target_price']}' is not a number", file=sys.stderr)
        sys.exit(1)

    origin = parsed["origin"].strip().upper()
    destination = parsed["destination"].strip().upper()
    if len(origin) != 3 or len(destination) != 3:
        print("ERROR: origin/destination must be 3-letter IATA codes", file=sys.stderr)
        sys.exit(1)

    new_route = {
        "name": parsed["route_name"],
        "origin": origin,
        "destination": destination,
        "departure_date": parsed["departure_date"],
        "return_date": parsed["return_date"],  # may be ""
        "target_price": target_price,
        "currency": parsed["currency"].strip().lower(),
    }

    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f) or {"routes": []}
    previous_routes = config.get("routes") or []
    config["routes"] = [new_route]  # replace - only one route tracked at a time

    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    print(f"Replaced route. Previous: {previous_routes} -> New: {new_route}")

    # Write a summary for the workflow to use in its issue comment
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    old_route_desc = (
        f"{previous_routes[0]['name']} ({previous_routes[0]['origin']}->{previous_routes[0]['destination']})"
        if previous_routes else "none"
    )
    confirmation = (
        f"Now tracking **{new_route['name']}**: {origin} -> {destination}, "
        f"depart {new_route['departure_date']}"
        + (f", return {new_route['return_date']}" if new_route['return_date'] else " (one-way)")
        + f", target {target_price} {new_route['currency']}"
        + f"\n\n(Replaced previous route: {old_route_desc})"
    )
    if summary_path:
        with open(summary_path, "a") as f:
            f.write(confirmation + "\n")

    # Also drop it in a file the workflow can read into the issue comment
    out_path = Path(os.environ.get("GITHUB_WORKSPACE", ".")) / "route_confirmation.txt"
    out_path.write_text(confirmation)


if __name__ == "__main__":
    main()
