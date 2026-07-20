#!/usr/bin/env python3
"""Horizontale patroondetectie: vind kennis die in meerdere projecten
terugkomt en dus kandidaat is voor promotie naar de klant- of bedrijfsbaseline.

Scant een map met projectrepo's (2 niveaus diep) op .claude/memory/topics/,
vergelijkt entries over projecten heen met hash-embeddings, en rapporteert
clusters die in 2 of meer verschillende projecten voorkomen.

Gebruik: python3 patterns.py <map-met-projecten> [drempel]
"""

import os
import signal

try:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except (AttributeError, ValueError):
    pass

import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memlib  # noqa: E402
import embeddings  # noqa: E402

DEFAULT_SIM = 0.45


def find_stores(base: str):
    stores = []
    base = os.path.abspath(base)
    for name in sorted(os.listdir(base)):
        for candidate in (os.path.join(base, name),
                          *(os.path.join(base, name, sub)
                            for sub in (sorted(os.listdir(os.path.join(base, name)))
                                        if os.path.isdir(os.path.join(base, name)) else []))):
            mem = os.path.join(candidate, ".claude", "memory")
            if os.path.isdir(os.path.join(mem, "topics")):
                stores.append((os.path.relpath(candidate, base), mem))
    # dedupe (project kan op beide niveaus matchen)
    seen, out = set(), []
    for label, mem in stores:
        if mem not in seen:
            seen.add(mem)
            out.append((label, mem))
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print("gebruik: patterns.py <map-met-projecten> [drempel]")
        return 1
    base = sys.argv[1]
    sim_min = float(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_SIM
    stores = find_stores(base)
    if len(stores) < 2:
        print(f"minder dan 2 projectstores gevonden onder {base}; niets te vergelijken")
        return 0

    emb = embeddings.HashEmbedder()
    items = []  # (project, entry, vector)
    for label, mem in stores:
        for e in memlib.all_entries(mem):
            items.append((label, e))
    vecs = emb.embed_batch([f"{e['title']}\n{e['body']}" for _l, e in items])

    print(f"{len(stores)} projecten, {len(items)} entries vergeleken "
          f"(drempel {sim_min})\n")
    used, clusters = set(), []
    for i in range(len(items)):
        if i in used:
            continue
        cluster = [i]
        for j in range(i + 1, len(items)):
            if j in used or items[i][0] == items[j][0]:
                continue  # zelfde project telt niet als patroon
            sim = sum(a * b for a, b in zip(vecs[i], vecs[j]))
            if sim >= sim_min:
                cluster.append(j)
        projects = {items[k][0] for k in cluster}
        if len(projects) >= 2:
            used.update(cluster)
            clusters.append(cluster)

    if not clusters:
        print("Geen terugkerende patronen gevonden.")
        return 0
    for n, cluster in enumerate(clusters, start=1):
        projects = sorted({items[k][0] for k in cluster})
        lead = items[cluster[0]][1]
        print(f"PATROON {n}: komt voor in {len(projects)} projecten "
              f"({', '.join(projects)})")
        for k in cluster:
            label, e = items[k]
            print(f"  [{label}/{e['topic']}] {e['title']}")
        print(f"  -> kandidaat voor promotie naar klant- of bedrijfsbaseline: "
              f"memlib.py add --store customer|global --topic {lead['topic']} ...\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
