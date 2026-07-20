#!/usr/bin/env python3
"""PostToolUse: registreer welke memory-bestanden Claude echt leest.

Vuurt op Read en Grep. Raakt het pad een topics-map van een actieve store,
dan komt er een regel bij in .usage.jsonl van die store. De index sorteert
topics vervolgens op werkelijk gebruik en /memory-report rapporteert erover.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memlib  # noqa: E402


def main() -> int:
    data = memlib.read_hook_input()
    ti = data.get("tool_input") or {}
    path = ti.get("file_path") or ti.get("path") or ""
    if not path:
        return 0
    path = os.path.abspath(path)
    root = memlib.find_project_root(data.get("cwd", os.getcwd()))
    cfg = memlib.load_config(root)
    for _label, store in memlib.read_stores(cfg, root):
        topics_dir = os.path.join(store, "topics") + os.sep
        if path.startswith(topics_dir) and path.endswith(".md"):
            memlib.record_usage(store, os.path.basename(path)[:-3])
    return 0


if __name__ == "__main__":
    sys.exit(main())
