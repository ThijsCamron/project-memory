#!/usr/bin/env python3
"""Gedeelde logica voor de project-memory plugin (v0.3).

Nieuw in v0.3:
  - secret-scrubbing: elke entry wordt voor opslag gescand op keys/tokens/
    wachtwoorden; treffers worden geredigeerd, private keys geweigerd
  - refs: entries kunnen verwijzen naar bestanden in de repo; de consolidatie
    verifieert die en archiveert entries waarvan de code niet meer bestaat
  - conflictdetectie: een nieuwe entry die sterk overlapt met een oude in
    hetzelfde topic (decisions/conventions) vervangt die; de oude gaat naar
    het archief met een vervangen-door-link
  - ADR-export: decisions (actief + vervangen) naar docs/adr/

Entry-formaat:
  ## 2026-07-16 | JWT in plaats van sessions
  keywords: auth, jwt
  refs: src/auth/jwt.py
  vervangt: 2026-06-01 | Sessions via load balancer
  <body>

Config (defaults < globale config < projectconfig < env):
  scope        project | global | both | off      (MEMORY_SCOPE)
  injection    hint | full | off                  (MEMORY_INJECTION)
  index_budget 300 | retrieval_budget 1000 | archive_days 30 | max_entries 50
"""

import datetime
import hashlib
import json
import os
import re
import sys

DEFAULT_TOPICS = ["decisions", "conventions", "gotchas", "context"]
CONFLICT_TOPICS = {"decisions", "conventions"}
CONFLICT_MIN_SHARED_KEYWORDS = 3

DEFAULTS = {
    "scope": "project",
    "injection": "hint",
    "index_budget": 300,
    "retrieval_budget": 1000,
    "archive_days": 30,
    "max_entries": 50,
    "retrieval": "hybrid",
    "embedding_backend": "hash",
    "embedding_model": "",
    "semantic_threshold": 0.25,
}

ENV_MAP = {
    "scope": "MEMORY_SCOPE",
    "injection": "MEMORY_INJECTION",
    "index_budget": "MEMORY_INDEX_BUDGET",
    "retrieval_budget": "MEMORY_RETRIEVAL_BUDGET",
    "archive_days": "MEMORY_ARCHIVE_DAYS",
    "max_entries": "MEMORY_MAX_ENTRIES",
    "retrieval": "MEMORY_RETRIEVAL",
    "embedding_backend": "MEMORY_EMBEDDING_BACKEND",
    "embedding_model": "MEMORY_EMBEDDING_MODEL",
    "semantic_threshold": "MEMORY_SEMANTIC_THRESHOLD",
}

STOPWORDS = set(
    """de het een en of maar want dus als dan dat dit die deze er is zijn was waren
    wordt worden met voor naar van in op aan bij uit om te ook nog al wel niet geen
    ik je jij we wij ze zij hij u hun ons onze mijn jouw heeft hebben had alle even
    kun plaats nieuwe the a an and or but so if then that this these those there is
    are was were be been being with for to of in on at by from about into over
    after before we you they he she it its our your their i not no do does did have
    has had will would can could should may use using used make makes made get gets
    got new now just like more most very""".split()
)

# ---------------------------------------------------------------- secrets ---

REDACTED = "[GEREDIGEERD]"

SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),                # weigeren
]

REDACT_PATTERNS = [
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                              # AWS
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"),                       # OpenAI/Anthropic-stijl
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),                   # GitHub
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),                # Slack
    re.compile(r"\beyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),  # JWT
    re.compile(r"(?i)\b(api[_-]?key|apikey|secret|token|password|passwd|wachtwoord)\b(\s*[=:]\s*)(\"[^\"]{6,}\"|'[^']{6,}'|\S{6,})"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.=]{16,}\b"),
]


def scrub_secrets(text: str):
    """(schone tekst, aantal redacties, geweigerd?)"""
    for p in SECRET_PATTERNS:
        if p.search(text):
            return text, 0, True
    count = 0

    def _sub_assign(m):
        nonlocal count
        count += 1
        return f"{m.group(1)}{m.group(2)}{REDACTED}"

    clean = text
    for p in REDACT_PATTERNS:
        if p.groups >= 3:
            clean, n = p.subn(_sub_assign, clean)
        else:
            clean, n = p.subn(REDACTED, clean)
            count += n
    return clean, count, False


# ----------------------------------------------------------------- config ---

def token_estimate(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def find_project_root(start: str) -> str:
    path = os.path.abspath(start or os.getcwd())
    probe = path
    while True:
        if os.path.isdir(os.path.join(probe, ".git")):
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            return path
        probe = parent


def project_store(root: str) -> str:
    return os.path.join(root, ".claude", "memory")


def global_store() -> str:
    return os.path.join(os.path.expanduser("~"), ".claude", "project-memory", "global")


def ensure_store(store: str) -> str:
    os.makedirs(os.path.join(store, "topics"), exist_ok=True)
    os.makedirs(os.path.join(store, "archive"), exist_ok=True)
    for name in DEFAULT_TOPICS:  # migratie v0.1
        old = os.path.join(store, f"{name}.md")
        new = os.path.join(store, "topics", f"{name}.md")
        if os.path.isfile(old) and not os.path.isfile(new):
            os.rename(old, new)
    return store


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_config(root: str) -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(_read_json(os.path.join(global_store(), "..", "config.json")))
    cfg.update(_read_json(os.path.join(project_store(root), "config.json")))
    for key, env in ENV_MAP.items():
        if env in os.environ:
            cfg[key] = os.environ[env]
    for key in ("index_budget", "retrieval_budget", "archive_days", "max_entries"):
        try:
            cfg[key] = int(cfg[key])
        except (TypeError, ValueError):
            cfg[key] = DEFAULTS[key]
    if cfg["scope"] not in ("project", "global", "both", "off"):
        cfg["scope"] = DEFAULTS["scope"]
    if cfg["injection"] not in ("hint", "full", "off"):
        cfg["injection"] = DEFAULTS["injection"]
    if cfg["retrieval"] not in ("keyword", "semantic", "hybrid"):
        cfg["retrieval"] = DEFAULTS["retrieval"]
    if cfg["embedding_backend"] not in ("hash", "local", "voyage", "openai"):
        cfg["embedding_backend"] = DEFAULTS["embedding_backend"]
    try:
        cfg["semantic_threshold"] = float(cfg["semantic_threshold"])
    except (TypeError, ValueError):
        cfg["semantic_threshold"] = DEFAULTS["semantic_threshold"]
    return cfg


def save_project_config(root: str, updates: dict) -> dict:
    store = ensure_store(project_store(root))
    path = os.path.join(store, "config.json")
    cfg = _read_json(path)
    cfg.update(updates)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    return cfg


def read_stores(cfg: dict, root: str):
    if cfg["scope"] == "off":
        return []
    stores = []
    if cfg["scope"] in ("project", "both"):
        stores.append(("project", project_store(root)))
    if cfg["scope"] in ("global", "both"):
        stores.append(("globaal", global_store()))
    return stores


def write_store(cfg: dict, root: str, override: str = None) -> str:
    choice = override or ("global" if cfg["scope"] == "global" else "project")
    store = global_store() if choice == "global" else project_store(root)
    return ensure_store(store)


def log(store: str, msg: str) -> None:
    try:
        with open(os.path.join(store, ".log"), "a", encoding="utf-8") as f:
            stamp = datetime.datetime.now().isoformat(timespec="seconds")
            f.write(f"{stamp} {msg}\n")
    except OSError:
        pass


# ---------------------------------------------------------------- entries ---

ENTRY_RE = re.compile(
    r"^## (?P<date>\d{4}-\d{2}-\d{2}) \| (?P<title>.+?)\s*\n"
    r"(?:keywords: (?P<keywords>.*?)\s*\n)?"
    r"(?:refs: (?P<refs>.*?)\s*\n)?"
    r"(?:vervangt: (?P<supersedes>.*?)\s*\n)?"
    r"(?P<body>.*?)(?=^## \d{4}-\d{2}-\d{2} \||\Z)",
    re.MULTILINE | re.DOTALL,
)


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9\-]+", "-", name.strip().lower()).strip("-")
    return s or "context"


def topic_path(store: str, topic: str) -> str:
    return os.path.join(store, "topics", f"{slug(topic)}.md")


def list_topics(store: str):
    d = os.path.join(store, "topics")
    if not os.path.isdir(d):
        return []
    return sorted(f[:-3] for f in os.listdir(d) if f.endswith(".md"))


def parse_entries(path: str, topic: str):
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        text = f.read()
    entries = []
    for m in ENTRY_RE.finditer(text):
        kw = [k.strip().lower() for k in (m.group("keywords") or "").split(",") if k.strip()]
        refs = [r.strip() for r in (m.group("refs") or "").split(",") if r.strip()]
        entries.append(
            {
                "topic": topic,
                "date": m.group("date"),
                "title": m.group("title").strip(),
                "keywords": kw,
                "refs": refs,
                "supersedes": (m.group("supersedes") or "").strip(),
                "body": m.group("body").strip(),
            }
        )
    return entries


def topic_entries(store: str, topic: str):
    return parse_entries(topic_path(store, topic), topic)


def all_entries(store: str):
    entries = []
    for t in list_topics(store):
        entries.extend(topic_entries(store, t))
    return entries


def entry_fingerprint(entry: dict) -> str:
    normalized = re.sub(r"\W+", " ", (entry["title"] + " " + entry["body"]).lower()).strip()
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def format_entry(entry: dict) -> str:
    lines = [f"## {entry['date']} | {entry['title']}"]
    lines.append("keywords: " + ", ".join(entry.get("keywords", [])))
    if entry.get("refs"):
        lines.append("refs: " + ", ".join(entry["refs"]))
    if entry.get("supersedes"):
        lines.append("vervangt: " + entry["supersedes"])
    lines.append(entry["body"])
    return "\n".join(lines) + "\n\n"


def write_topic(store: str, topic: str, entries: list) -> None:
    path = topic_path(store, topic)
    if not entries:
        if os.path.isfile(path):
            os.remove(path)
        return
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(format_entry(e))


def archive_entries(store: str, topic: str, entries: list, note: str = "") -> None:
    if not entries:
        return
    path = os.path.join(store, "archive", f"{slug(topic)}.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for e in entries:
            e = dict(e)
            if note:
                e["body"] = e["body"].rstrip() + f"\n{note}"
            f.write(format_entry(e))


def extract_refs(text: str, root: str, limit: int = 4):
    """Bestandspaden uit tekst die echt bestaan in de repo."""
    if not root:
        return []
    candidates = re.findall(r"[\w][\w./\-]*\.[A-Za-z]{1,6}\b", text)
    refs, seen = [], set()
    for c in candidates:
        c = c.strip(".")
        if c in seen or c.count(".") > 3:
            continue
        seen.add(c)
        if os.path.isfile(os.path.join(root, c)):
            refs.append(c)
        if len(refs) >= limit:
            break
    return refs


def _find_conflicts(store: str, topic: str, candidate: dict):
    """Oude entries in hetzelfde topic met >= N gedeelde keywords."""
    if slug(topic) not in CONFLICT_TOPICS:
        return []
    new_kw = set(candidate["keywords"])
    conflicts = []
    for e in topic_entries(store, topic):
        if len(new_kw & set(e["keywords"])) >= CONFLICT_MIN_SHARED_KEYWORDS:
            conflicts.append(e)
    return conflicts


def append_entry(store: str, topic: str, title: str, keywords, body: str,
                 refs=None) -> dict:
    """Voeg toe met scrubbing, dedupe en conflictdetectie.

    Resultaat: {added, reason, redacted, superseded:[titels]}
    """
    ensure_store(store)
    combined = f"{title}\n{body}"
    clean, redacted, refused = scrub_secrets(combined)
    if refused:
        return {"added": False, "reason": "geweigerd: bevat private key",
                "redacted": 0, "superseded": []}
    title_clean, _, body_clean = clean.partition("\n")

    candidate = {
        "topic": slug(topic),
        "date": datetime.date.today().isoformat(),
        "title": title_clean.strip()[:120],
        "keywords": [k.strip().lower() for k in keywords if k.strip()][:8],
        "refs": list(refs or [])[:4],
        "supersedes": "",
        "body": body_clean.strip(),
    }
    if entry_fingerprint(candidate) in {entry_fingerprint(e) for e in all_entries(store)}:
        return {"added": False, "reason": "duplicaat", "redacted": redacted,
                "superseded": []}

    conflicts = _find_conflicts(store, topic, candidate)
    if conflicts:
        remaining = [e for e in topic_entries(store, topic)
                     if entry_fingerprint(e) not in {entry_fingerprint(c) for c in conflicts}]
        write_topic(store, topic, remaining)
        note = f"vervangen-door: {candidate['date']} | {candidate['title']}"
        archive_entries(store, topic, conflicts, note=note)
        newest = max(conflicts, key=lambda e: e["date"])
        candidate["supersedes"] = f"{newest['date']} | {newest['title']}"

    with open(topic_path(store, topic), "a", encoding="utf-8") as f:
        f.write(format_entry(candidate))
    return {"added": True, "reason": "", "redacted": redacted,
            "superseded": [c["title"] for c in conflicts]}


def extract_keywords(text: str, limit: int = 6):
    words = [w.strip(".") for w in re.findall(r"[a-zA-Z_][a-zA-Z0-9_\-\.]{2,}", text.lower())]
    freq = {}
    for w in words:
        if w in STOPWORDS:
            continue
        freq[w] = freq.get(w, 0) + 1
    ranked = sorted(freq, key=lambda w: (-freq[w], w))
    return ranked[:limit]


# ------------------------------------------------------ index & retrieval ---

def topic_summary(store: str, topic: str) -> dict:
    entries = topic_entries(store, topic)
    kw_freq = {}
    for e in entries:
        for k in e["keywords"]:
            kw_freq[k] = kw_freq.get(k, 0) + 1
    top_kw = sorted(kw_freq, key=lambda k: (-kw_freq[k], k))[:5]
    latest = max((e["date"] for e in entries), default="")
    return {"topic": topic, "count": len(entries), "keywords": top_kw, "latest": latest}


def rebuild_index(cfg: dict, root: str) -> str:
    lines, total = [], 0
    for label, store in read_stores(cfg, root):
        for t in list_topics(store):
            s = topic_summary(store, t)
            if not s["count"]:
                continue
            total += s["count"]
            kw = ", ".join(s["keywords"])
            rel = os.path.join(store, "topics", f"{t}.md")
            lines.append(f"- [{label}] {t} ({s['count']} entries; {kw}) -> {rel}")
    body = "\n".join(lines) if lines else "(nog geen memories)"
    index = (
        "# Memory-index\n"
        f"Scope: {cfg['scope']}. {total} entries in totaal.\n"
        "Details nodig over een onderwerp? Lees dan zelf het genoemde bestand "
        "met Read of doorzoek het met Grep. Laad nooit alle bestanden tegelijk.\n\n"
        + body + "\n"
    )
    for _label, store in read_stores(cfg, root):
        if os.path.isdir(store):
            try:
                with open(os.path.join(store, "index.md"), "w", encoding="utf-8") as f:
                    f.write(index)
            except OSError:
                pass
    return index


def _prompt_words(prompt: str) -> set:
    return {w for w in re.findall(r"\w+", prompt.lower()) if w not in STOPWORDS and len(w) > 2}


def score_entry(entry: dict, prompt_words: set) -> int:
    score = 0
    for kw in entry["keywords"]:
        for part in re.findall(r"\w+", kw):
            if part in prompt_words:
                score += 3
    for w in re.findall(r"\w+", entry["title"].lower()):
        if w not in STOPWORDS and w in prompt_words:
            score += 2
    body_words = set(re.findall(r"\w+", entry["body"].lower()))
    score += len((body_words - STOPWORDS) & prompt_words) // 3
    return score


def matching_topics(cfg: dict, root: str, prompt: str):
    pw = _prompt_words(prompt)
    if not pw:
        return []
    hits = []
    for label, store in read_stores(cfg, root):
        for t in list_topics(store):
            score = sum(score_entry(e, pw) for e in topic_entries(store, t))
            if score > 0:
                hits.append((label, t, os.path.join(store, "topics", f"{t}.md"), score))
    hits.sort(key=lambda h: -h[3])
    return hits


def select_relevant(cfg: dict, root: str, prompt: str, budget_tokens: int):
    pw = _prompt_words(prompt)
    if not pw:
        return []
    scored = []
    for label, store in read_stores(cfg, root):
        for e in all_entries(store):
            s = score_entry(e, pw)
            if s > 0:
                scored.append((s, dict(e, herkomst=label)))
    scored.sort(key=lambda p: (-p[0], p[1]["date"]))
    picked, used = [], 0
    for _s, e in scored:
        cost = token_estimate(e["title"] + e["body"])
        if used + cost > budget_tokens:
            continue
        picked.append(e)
        used += cost
    return picked


# ------------------------------------------------------------- ADR-export ---

def export_adr(root: str) -> list:
    """Decisions (actief + vervangen) naar docs/adr/NNNN-slug.md."""
    store = project_store(root)
    active = topic_entries(store, "decisions")
    archived = parse_entries(os.path.join(store, "archive", "decisions.md"), "decisions")

    records = []
    for e in active:
        records.append((e, "Geaccepteerd", ""))
    for e in archived:
        m = re.search(r"^vervangen-door: (.+)$", e["body"], re.MULTILINE)
        if m:
            records.append((e, "Vervangen", m.group(1).strip()))
    records.sort(key=lambda r: (r[0]["date"], 0 if r[1] == "Vervangen" else 1))

    out_dir = os.path.join(root, "docs", "adr")
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for i, (e, status, superseded_by) in enumerate(records, start=1):
        body = re.sub(r"^vervangen-door: .+$", "", e["body"], flags=re.MULTILINE).strip()
        lines = [
            f"# ADR-{i:04d}: {e['title']}",
            "",
            f"Datum: {e['date']}",
            f"Status: {status}" + (f" (door: {superseded_by})" if superseded_by else ""),
        ]
        if e.get("supersedes"):
            lines.append(f"Vervangt: {e['supersedes']}")
        if e.get("refs"):
            lines.append("Code: " + ", ".join(e["refs"]))
        lines += ["", "## Context en besluit", "", body, ""]
        path = os.path.join(out_dir, f"{i:04d}-{slug(e['title'])[:50]}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        written.append(path)
    return written


# -------------------------------------------------------------- hook util ---

def read_hook_input() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        return {}


def emit_context(event_name: str, context: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": event_name, "additionalContext": context}}))


# -------------------------------------------------------------------- CLI ---

def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add")
    p_add.add_argument("--root", default=os.getcwd())
    p_add.add_argument("--topic", default="context")
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--keywords", default="")
    p_add.add_argument("--body", required=True)
    p_add.add_argument("--refs", default="")
    p_add.add_argument("--store", choices=["project", "global"], default=None)

    p_cfg = sub.add_parser("config")
    p_cfg.add_argument("--root", default=os.getcwd())
    p_cfg.add_argument("--scope", choices=["project", "global", "both", "off"])
    p_cfg.add_argument("--injection", choices=["hint", "full", "off"])
    p_cfg.add_argument("--index-budget", type=int, dest="index_budget")
    p_cfg.add_argument("--retrieval-budget", type=int, dest="retrieval_budget")
    p_cfg.add_argument("--retrieval", choices=["keyword", "semantic", "hybrid"])
    p_cfg.add_argument("--embedding-backend", dest="embedding_backend",
                       choices=["hash", "local", "voyage", "openai"])
    p_cfg.add_argument("--embedding-model", dest="embedding_model")
    p_cfg.add_argument("--show", action="store_true")

    p_re = sub.add_parser("reindex")
    p_re.add_argument("--root", default=os.getcwd())

    p_adr = sub.add_parser("export-adr")
    p_adr.add_argument("--root", default=os.getcwd())

    args = parser.parse_args()
    root = find_project_root(args.root)
    cfg = load_config(root)

    if args.cmd == "add":
        store = write_store(cfg, root, override=args.store)
        keywords = args.keywords.split(",") if args.keywords else extract_keywords(args.body)
        refs = [r.strip() for r in args.refs.split(",") if r.strip()] \
            or extract_refs(args.body, root if store != global_store() else "")
        result = append_entry(store, args.topic, args.title, keywords, args.body, refs=refs)
        rebuild_index(cfg, root)
        try:
            import embeddings
            embeddings.sync(store, cfg)
        except Exception:
            pass
        where = "globaal" if store == global_store() else "project"
        if result["added"]:
            msg = f"opgeslagen in {where}/topics/{slug(args.topic)}.md"
            if result["redacted"]:
                msg += f" ({result['redacted']} geheim(en) geredigeerd)"
            if result["superseded"]:
                msg += " | vervangt: " + "; ".join(result["superseded"])
            print(msg)
        else:
            print(f"niet opgeslagen: {result['reason']}")
    elif args.cmd == "config":
        updates = {k: getattr(args, k) for k in
                   ("scope", "injection", "index_budget", "retrieval_budget",
                    "retrieval", "embedding_backend", "embedding_model")
                   if getattr(args, k, None) is not None}
        if updates:
            save_project_config(root, updates)
            cfg = load_config(root)
            rebuild_index(cfg, root)
        print(json.dumps(cfg, indent=2))
    elif args.cmd == "reindex":
        print(rebuild_index(cfg, root))
    elif args.cmd == "export-adr":
        written = export_adr(root)
        if written:
            print(f"{len(written)} ADR-bestanden geschreven naar docs/adr/:")
            for p in written:
                print(f"  {p}")
        else:
            print("Geen decisions gevonden om te exporteren.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
