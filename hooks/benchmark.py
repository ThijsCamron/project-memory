#!/usr/bin/env python3
"""Benchmark voor de retrieval-kwaliteit van project-memory.

Meet met een gouden vragenset (realistische NL-vragen -> verwacht topic) hoe
vaak de hint-injectie het juiste topic in de top-3 en op plek 1 zet, per
retrieval-modus en drempel. Gebruik dit om instellingen te tunen op basis van
metingen in plaats van gevoel.

Gebruik:
  python3 benchmark.py --root <projectpad>            # huidige config meten
  python3 benchmark.py --root <projectpad> --sweep    # configuraties vergelijken

De gouden set hieronder is afgestemd op de Aimigo-baseline plus een
kickoff-project; pas hem aan aan je eigen stores voor eigen metingen.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memlib  # noqa: E402
import prompt_submit  # noqa: E402

# (vraag, set van goedgekeurde topics)
GOLD = [
    ("hoe zetten we een nieuw project live op k3s", {"deployment", "prod-overgang"}),
    ("welke php versie en frameworks gebruiken we standaard", {"backend-stack"}),
    ("hoe richten we een react frontend in", {"frontend-stack"}),
    ("hoe structureren we de domeinlaag en bounded contexts", {"architectuur-ddd"}),
    ("hoe schrijven we unit en integration tests", {"testing"}),
    ("welke phpstan en code style regels gelden er", {"static-analysis-codestyle"}),
    ("hoe zit het met cors en rate limiting", {"security"}),
    ("hoe doen we database migraties", {"database"}),
    ("waar loggen we fouten naartoe", {"observability"}),
    ("wat zijn onze afspraken rond commits en env bestanden", {"conventies"}),
    ("welke llm gebruiken we voor ai projecten", {"backend-stack"}),
    ("hoe definieren we tools voor het taalmodel", {"tool-contract"}),
    ("welke dingen bouwen we bewust niet zelf", {"ontwikkelstrategie"}),
    ("hoe rollen we terug bij een mislukte deploy", {"deployment", "prod-overgang"}),
    ("hoe gaan we om met secrets en wachtwoorden", {"database", "security"}),
    ("waarom kozen we voor postgres met pgbouncer", {"decisions"}),
    ("hoe vaak verandert de dagplanning van de monteurs", {"requirements"}),
    ("wat wil de klant verbeteren aan het offerteproces", {"requirements"}),
    ("waar moet ik op letten bij de daktekeningen", {"gotchas", "requirements"}),
    ("wat voor soort bedrijf is deze klant eigenlijk", {"klanten"}),
    ("zullen we een mobiele app maken", {"scope-nee"}),
    ("wat is de rate limit van de monta api", {"gotchas"}),
    ("hoe snel beslist peter over grote keuzes", {"klanten"}),
    ("welke tests horen er bij nieuwe endpoints", {"conventions", "testing"}),
    ("kiezen we canary releases of handmatig deployen", {"decisions"}),
    ("welke eisen stelde de klant aan het tekenen van daken", {"requirements", "gotchas"}),
]


def run_config(cfg, root):
    top1 = top3 = 0
    latencies = []
    misses = []
    for query, expected in GOLD:
        t0 = time.perf_counter()
        hits = prompt_submit.topic_hits(cfg, root, query)
        latencies.append((time.perf_counter() - t0) * 1000)
        ranked = [topic for (_label, topic) in hits.keys()]
        if ranked and ranked[0] in expected:
            top1 += 1
        if any(t in expected for t in ranked[:3]):
            top3 += 1
        else:
            misses.append((query, sorted(expected), ranked[:3]))
    n = len(GOLD)
    return {
        "top1": top1 / n, "top3": top3 / n,
        "p50_ms": sorted(latencies)[n // 2],
        "max_ms": max(latencies),
        "misses": misses,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=os.getcwd())
    parser.add_argument("--sweep", action="store_true")
    args = parser.parse_args()
    root = memlib.find_project_root(args.root)
    base = memlib.load_config(root)

    if not args.sweep:
        r = run_config(base, root)
        print(f"config: retrieval={base['retrieval']} "
              f"threshold={base['semantic_threshold']}")
        print(f"top-1: {r['top1']:.0%} | top-3: {r['top3']:.0%} | "
              f"latency p50 {r['p50_ms']:.0f}ms max {r['max_ms']:.0f}ms")
        for q, exp, got in r["misses"]:
            print(f"  MIS: '{q}' verwacht {exp}, kreeg {got}")
        return 0

    print(f"{'modus':10s} {'drempel':8s} {'top-1':>6s} {'top-3':>6s} {'p50':>7s}")
    results = []
    for mode in ("keyword", "semantic", "hybrid"):
        thresholds = [0.0] if mode == "keyword" else [0.12, 0.18, 0.25, 0.35]
        for th in thresholds:
            cfg = dict(base, retrieval=mode, semantic_threshold=th)
            r = run_config(cfg, root)
            results.append((mode, th, r))
            print(f"{mode:10s} {th:<8} {r['top1']:>6.0%} {r['top3']:>6.0%} "
                  f"{r['p50_ms']:>6.0f}ms")
    best = max(results, key=lambda x: (x[2]["top3"], x[2]["top1"], -x[1]))
    print(f"\nBESTE: {best[0]} met drempel {best[1]} "
          f"(top-3 {best[2]['top3']:.0%}, top-1 {best[2]['top1']:.0%})")
    for q, exp, got in best[2]["misses"]:
        print(f"  resterende mis: '{q}' verwacht {exp}, kreeg {got}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
