#!/usr/bin/env python3
"""UserPromptSubmit: injectie volgens config.

Retrieval-modus (config "retrieval"):
  keyword   trefwoord-scoring (v0.2-gedrag)
  semantic  cosine similarity via de embedding-index (.index.db)
  hybrid    beide; een topic telt als het via een van beide routes matcht

Injectie-modus: hint (verwijzing, Claude pullt zelf), full (hele entries), off.
Met --search "termen" werkt dit script als los zoekcommando.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memlib  # noqa: E402

try:
    import embeddings
except Exception:  # semantiek is optioneel; keyword werkt altijd
    embeddings = None


def _semantic_topic_hits(cfg, root):
    def inner(prompt):
        hits = {}
        if not embeddings or cfg["retrieval"] == "keyword":
            return hits
        for label, store in memlib.read_stores(cfg, root):
            for topic, score in embeddings.topic_scores(store, cfg, prompt).items():
                if score >= cfg["semantic_threshold"]:
                    path = os.path.join(store, "topics", f"{topic}.md")
                    key = (label, topic)
                    hits[key] = max(hits.get(key, (0, path))[0], score), path
        return {k: {"score": v[0], "path": v[1]} for k, v in hits.items()}
    return inner


def topic_hits(cfg, root, prompt):
    """{(label, topic): {score, path}} volgens de ingestelde retrieval-modus."""
    hits = {}
    if cfg["retrieval"] in ("semantic", "hybrid"):
        hits.update(_semantic_topic_hits(cfg, root)(prompt))
    if cfg["retrieval"] in ("keyword", "hybrid") or not hits:
        for label, topic, path, score in memlib.matching_topics(cfg, root, prompt):
            key = (label, topic)
            norm = min(1.0, score / 10.0)
            if key not in hits or norm > hits[key]["score"]:
                hits[key] = {"score": norm, "path": path}
    return dict(sorted(hits.items(), key=lambda kv: -kv[1]["score"]))


def render_full(entries) -> str:
    return "\n\n".join(
        f"[{e.get('herkomst','project')}/{e['topic']} | {e['date']}] {e['title']}\n{e['body']}"
        for e in entries)


def full_entries(cfg, root, prompt, budget):
    picked = memlib.select_relevant(cfg, root, prompt, budget)
    if picked or not embeddings or cfg["retrieval"] == "keyword":
        return picked
    used, out, seen = 0, [], set()
    for label, store in memlib.read_stores(cfg, root):
        for score, e in embeddings.search(store, cfg, prompt, top_k=10):
            if score < cfg["semantic_threshold"]:
                continue
            key = (e["title"], e["date"])
            if key in seen:
                continue
            seen.add(key)
            cost = memlib.token_estimate(e["title"] + e["body"])
            if used + cost > budget:
                continue
            out.append(dict(e, herkomst=label))
            used += cost
    return out


def cli_search(query: str) -> int:
    root = memlib.find_project_root(os.getcwd())
    cfg = memlib.load_config(root)
    if cfg["scope"] == "off":
        print("Memory staat uit voor dit project (scope=off).")
        return 0
    entries = full_entries(cfg, root, query, budget=4000)
    if not entries:
        print(f"Geen memories gevonden voor: {query}")
        return 0
    print(f"{len(entries)} match(es) (scope={cfg['scope']}, retrieval={cfg['retrieval']}):\n")
    print(render_full(entries))
    return 0


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--search":
        return cli_search(" ".join(sys.argv[2:]))

    data = memlib.read_hook_input()
    prompt = data.get("prompt", "") or ""
    if not prompt or prompt.lstrip().startswith("/"):
        return 0

    root = memlib.find_project_root(data.get("cwd", os.getcwd()))
    cfg = memlib.load_config(root)
    if cfg["scope"] == "off" or cfg["injection"] == "off":
        return 0

    if cfg["injection"] == "full":
        entries = full_entries(cfg, root, prompt, cfg["retrieval_budget"])
        if not entries:
            return 0
        context = "Relevante projectmemory (automatisch opgehaald):\n\n" + render_full(entries)
    else:  # hint
        hits = topic_hits(cfg, root, prompt)
        if not hits:
            return 0
        lines = [f"- [{label}] {topic}: {info['path']}"
                 for (label, topic), info in list(hits.items())[:3]]
        context = ("Over dit onderwerp bestaat opgeslagen memory. Lees bij twijfel "
                   "het bestand met Read:\n" + "\n".join(lines))

    memlib.emit_context("UserPromptSubmit", context)
    return 0


if __name__ == "__main__":
    sys.exit(main())
