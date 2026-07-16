#!/usr/bin/env python3
"""SessionStart: injecteer de merged index (pull-model), binnen index_budget.

Respecteert de scope-config: bij scope=off wordt niets geinjecteerd.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memlib  # noqa: E402


def main() -> int:
    data = memlib.read_hook_input()
    root = memlib.find_project_root(data.get("cwd", os.getcwd()))
    cfg = memlib.load_config(root)
    if cfg["scope"] == "off":
        return 0

    stores = memlib.read_stores(cfg, root)
    if not any(memlib.list_topics(s) for _l, s in stores):
        return 0  # nergens memory: injecteer niets, kost niets

    index = memlib.rebuild_index(cfg, root)
    budget = cfg["index_budget"]
    if memlib.token_estimate(index) > budget:
        lines = index.splitlines()
        kept, used = [], 0
        for line in lines:
            cost = memlib.token_estimate(line)
            if used + cost > budget - 10:
                kept.append(f"... ({len(lines) - len(kept)} regels weggelaten wegens tokenbudget)")
                break
            kept.append(line)
            used += cost
        index = "\n".join(kept)

    memlib.emit_context("SessionStart", index)
    for _label, store in stores:
        if os.path.isdir(store):
            memlib.log(store, f"session_start: index ({memlib.token_estimate(index)} tokens, scope={cfg['scope']})")
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
