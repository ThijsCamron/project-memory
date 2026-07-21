#!/usr/bin/env python3
"""Consolidatie per store: dedupe, archiveer oude entries, cap per topic,
herbouw de index. Met --status een overzicht van alle actieve stores."""

import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memlib  # noqa: E402


def verify_refs(store: str, root: str) -> int:
    """Archiveer entries waarvan een gerefereerd bestand niet meer bestaat."""
    stale_total = 0
    for topic in memlib.list_topics(store):
        entries = memlib.topic_entries(store, topic)
        if any(e.get("doc") for e in entries):
            continue
        fresh, stale = [], []
        for e in entries:
            missing = [r for r in e.get("refs", []) if not os.path.isfile(os.path.join(root, r))]
            (stale if missing else fresh).append((e, missing))
        if stale:
            memlib.write_topic(store, topic, [e for e, _m in fresh])
            for e, missing in stale:
                memlib.archive_entries(store, topic, [e],
                    note="verouderd: bestaat niet meer in de code: " + ", ".join(missing))
            stale_total += len(stale)
    return stale_total


def consolidate_store(store: str, cfg: dict, root: str = None) -> dict:
    for issue in memlib.validate_store(store):
        memlib.log(store, f"validator: {issue}")
        print(f"  validator [{os.path.basename(store)}]: {issue}")
    memlib.cleanup_temp_files(store)
    cutoff = (datetime.date.today() - datetime.timedelta(days=cfg["archive_days"])).isoformat()
    stats = {"deduped": 0, "archived": 0, "verouderd": 0}
    if root and store == memlib.project_store(root):
        stats["verouderd"] = verify_refs(store, root)
    lock_ctx = memlib.store_lock(store)
    lock_ctx.__enter__()
    for topic in memlib.list_topics(store):
        entries = memlib.topic_entries(store, topic)
        if any(e.get("doc") for e in entries):
            continue  # vrij document: laten zoals de gebruiker het neerzette
        unique, seen = [], set()
        for e in entries:
            fp = memlib.entry_fingerprint(e)
            if fp in seen:
                stats["deduped"] += 1
                continue
            seen.add(fp)
            unique.append(e)
        old = [e for e in unique if e["date"] < cutoff]
        keep = sorted((e for e in unique if e["date"] >= cutoff), key=lambda e: e["date"])
        if len(keep) > cfg["max_entries"]:
            overflow = keep[: len(keep) - cfg["max_entries"]]
            keep = keep[len(keep) - cfg["max_entries"]:]
            old.extend(overflow)
        memlib.archive_entries(store, topic, old)
        stats["archived"] += len(old)
        memlib.write_topic(store, topic, keep)
    marker = os.path.join(store, ".last_consolidation")
    with open(marker, "w", encoding="utf-8") as f:
        f.write(datetime.datetime.now().isoformat())
    lock_ctx.__exit__(None, None, None)
    return stats


def status(cfg: dict, root: str) -> int:
    memlib.rebuild_index(cfg, root)  # status toont altijd de actuele werkelijkheid
    print(f"Scope: {cfg['scope']} | injectie: {cfg['injection']}")
    print(f"Budgetten: index {cfg['index_budget']} tokens, retrieval {cfg['retrieval_budget']} tokens")
    stores = memlib.read_stores(cfg, root)
    if not stores:
        print("Memory staat uit voor dit project. Aanzetten: /project-memory:memory-config scope project")
        return 0
    for label, store in stores:
        entries = memlib.all_entries(store)
        index_path = os.path.join(store, "index.md")
        index_tokens = 0
        if os.path.isfile(index_path):
            with open(index_path, encoding="utf-8") as f:
                index_tokens = memlib.token_estimate(f.read())
        print(f"\n[{label}] {store}")
        if not entries:
            print("  (leeg)")
            continue
        print(f"  index: ~{index_tokens} tokens bij sessiestart, {len(entries)} entries")
        for t in memlib.list_topics(store):
            s = memlib.topic_summary(store, t)
            if s["count"]:
                print(f"  topics/{t}.md  {s['count']:3d} entries  laatste: {s['latest']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=os.getcwd())
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    root = memlib.find_project_root(args.root)
    cfg = memlib.load_config(root)

    if args.status:
        return status(cfg, root)
    if cfg["scope"] == "off":
        return 0

    totals = {"deduped": 0, "archived": 0, "verouderd": 0}
    for _label, store in memlib.read_stores(cfg, root):
        if not os.path.isdir(store):
            continue
        stats = consolidate_store(store, cfg, root=root)
        for k in totals:
            totals[k] += stats.get(k, 0)
        memlib.log(store, f"consolidate: {stats['deduped']} dedupes, "
                   f"{stats['archived']} gearchiveerd, {stats.get('verouderd', 0)} verouderd")
    try:
        import embeddings
        for _label, store in memlib.read_stores(cfg, root):
            if os.path.isdir(store):
                embeddings.sync(store, cfg)
    except Exception:
        pass
    memlib.rebuild_index(cfg, root)
    print(f"Consolidatie klaar: {totals['deduped']} duplicaten verwijderd, "
          f"{totals['archived']} gearchiveerd, {totals['verouderd']} verouderd "
          f"(code-referentie verdwenen).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
