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
import signal
import sys

try:  # nette exit als output door head/less wordt afgekapt
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except (AttributeError, ValueError):
    pass

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
        if len(memlib._prompt_words(prompt)) < 3:
            return hits  # te weinig signaal voor semantiek; keyword-pad dekt dit
        emb_name = embeddings.get_embedder(cfg).name
        th = memlib.effective_semantic_threshold(cfg, emb_name)
        for label, store in memlib.read_stores(cfg, root):
            for topic, score in embeddings.topic_scores(store, cfg, prompt).items():
                if score >= th:
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
    parts = []
    for e in entries:
        body = e["body"]
        if e.get("doc") and len(body) > 1200:
            body = body[:1200] + f"\n... (vrij document, lees volledig: {e.get('path','')})"
        parts.append(f"[{e.get('herkomst','project')}/{e['topic']} | {e['date']}] {e['title']}\n{body}")
    return "\n\n".join(parts)


def full_entries(cfg, root, prompt, budget):
    picked = memlib.select_relevant(cfg, root, prompt, budget)
    if picked or not embeddings or cfg["retrieval"] == "keyword":
        return picked
    used, out, seen = 0, [], set()
    th = memlib.effective_semantic_threshold(cfg, embeddings.get_embedder(cfg).name)
    for label, store in memlib.read_stores(cfg, root):
        for score, e in embeddings.search(store, cfg, prompt, top_k=10):
            if score < th:
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
        waar = ", ".join(label for label, _s in memlib.read_stores(cfg, root)) or "geen stores actief"
        print(f"Geen memories gevonden voor: {query} (gezocht in: {waar})")
        return 0
    waar = ", ".join(label for label, _s in memlib.read_stores(cfg, root))
    print(f"{len(entries)} match(es) (gezocht in: {waar}):\n")
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
    if cfg["scope"] == "off":
        return 0

    # direct opslaan: "onthoud: ..." wacht niet op de Stop-hook
    stripped = prompt.strip()
    for prefix in ("onthoud:", "remember:"):
        if stripped.lower().startswith(prefix):
            text = stripped[len(prefix):].strip()
            if not text:
                break
            topic = memlib.classify(text, memlib.get_triggers(cfg)) or "gotchas"
            store = memlib.write_store(cfg, root)
            result = memlib.append_entry(store, topic, memlib.title_from(text),
                                         memlib.extract_keywords(text), text,
                                         refs=memlib.extract_refs(text, root), cfg=cfg)
            if result["added"]:
                memlib.rebuild_index(cfg, root)
                try:
                    import embeddings
                    embeddings.sync(store, cfg)
                except Exception:
                    pass
                note = f"Direct opgeslagen in topics/{memlib.slug(topic)}.md"
                if result["redacted"]:
                    note += f" ({result['redacted']} geheim(en) geredigeerd)"
                if result["superseded"]:
                    note += " | vervangt: " + "; ".join(result["superseded"])
                memlib.emit_context("UserPromptSubmit", note)
                memlib.log(store, f"prompt_submit: direct opgeslagen in {topic}")
            return 0

    if cfg["injection"] == "off":
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
        # scope-nee is een waarschuwing, geen gewone hint: titels direct tonen
        warn_lines = []
        for (label, topic), _info in hits.items():
            if topic != "scope-nee":
                continue
            for _lab, store in memlib.read_stores(cfg, root):
                if _lab == label:
                    pw = memlib._prompt_words(prompt)
                    for e in memlib.topic_entries(store, "scope-nee"):
                        if memlib.score_entry(e, pw) > 0:
                            warn_lines.append(f"  - {e['title']}: {e['body'][:140]}")
        normal = [f"- [{label}] {topic}: {info['path']}"
                  for (label, topic), info in list(hits.items())[:3]
                  if topic != "scope-nee"]
        parts = []
        if warn_lines:
            parts.append("WAARSCHUWING: dit raakt mogelijk iets dat expliciet "
                         "BUITEN scope is afgesproken:\n" + "\n".join(warn_lines[:3])
                         + "\nBenoem dit expliciet voordat je eraan begint te bouwen.")
        if normal:
            parts.append("Over dit onderwerp bestaat opgeslagen memory. Lees bij "
                         "twijfel het bestand met Read:\n" + "\n".join(normal))
        if not parts:
            return 0
        context = "\n\n".join(parts)

    memlib.emit_context("UserPromptSubmit", context)
    for _label, store in memlib.read_stores(cfg, root):
        if os.path.isdir(store):
            memlib.log(store, f"prompt_submit: injectie ({memlib.token_estimate(context)} tokens, {cfg['injection']})")
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
