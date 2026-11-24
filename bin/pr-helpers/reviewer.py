#!/usr/bin/env python3
"""Print who reviews each round: the machine config's `reviewer`, `copilot` or `claude`.

pr and ghbody read it from here, so `pr`, `gh pc` and `gh pb` share one field and one check.
A config without the key (or no config file) is `copilot`. Any other value, or a config that isn't
JSON, prints why on stderr and exits 2. Stdlib only: the scripts run without lib/ on the path, and
the file is lib/config.py's CONFIG_FILE.
"""
import json
import os
import sys

CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".config", "skuld", "config.json")
REVIEWERS = ("copilot", "claude")


def reviewer_in(config_file):
    try:
        with open(config_file, encoding="utf8") as f:
            config = json.load(f)
    except FileNotFoundError:
        return REVIEWERS[0]
    except (OSError, ValueError) as error:
        raise SystemExit(f"{config_file} can't be read: {error}")
    value = config.get("reviewer", REVIEWERS[0]) if isinstance(config, dict) else REVIEWERS[0]
    if value not in REVIEWERS:
        raise SystemExit(f'{config_file}: "reviewer" is {value!r}; it must be one of {", ".join(REVIEWERS)}')
    return value


if __name__ == "__main__":
    try:
        print(reviewer_in(CONFIG_FILE))
    except SystemExit as refusal:
        print(refusal, file=sys.stderr)
        sys.exit(2)
