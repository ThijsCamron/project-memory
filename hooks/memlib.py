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
import signal
import sys
import tempfile
from contextlib import contextmanager

try:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except (AttributeError, ValueError):
    pass

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
    "conflict_threshold": 0.8,
    "triggers": [],
    "embedding_backend": "auto",
    "embedding_model": "",
    "semantic_threshold": 0.25,
    "customer": "",
    "price_eur_per_mtok": 2.75,
}

ENV_MAP = {
    "scope": "MEMORY_SCOPE",
    "injection": "MEMORY_INJECTION",
    "index_budget": "MEMORY_INDEX_BUDGET",
    "retrieval_budget": "MEMORY_RETRIEVAL_BUDGET",
    "archive_days": "MEMORY_ARCHIVE_DAYS",
    "max_entries": "MEMORY_MAX_ENTRIES",
    "retrieval": "MEMORY_RETRIEVAL",
    "conflict_threshold": "MEMORY_CONFLICT_THRESHOLD",
    "embedding_backend": "MEMORY_EMBEDDING_BACKEND",
    "embedding_model": "MEMORY_EMBEDDING_MODEL",
    "customer": "MEMORY_CUSTOMER",
    "price_eur_per_mtok": "MEMORY_PRICE_EUR_PER_MTOK",
    "semantic_threshold": "MEMORY_SEMANTIC_THRESHOLD",
}

STOPWORDS = set(
    """de het een en of maar want dus als dan dat dit die deze er is zijn was waren
    wat hoe waar wie waarom wanneer welke welk hoeveel zullen moet moeten kan kunnen doen
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


def customer_store(customer: str) -> str:
    return os.path.join(os.path.expanduser("~"), ".claude", "project-memory",
                        "customers", slug(customer))


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
    try:
        cfg["conflict_threshold"] = float(cfg["conflict_threshold"])
    except (TypeError, ValueError):
        cfg["conflict_threshold"] = DEFAULTS["conflict_threshold"]
    try:
        cfg["price_eur_per_mtok"] = float(cfg["price_eur_per_mtok"])
    except (TypeError, ValueError):
        cfg["price_eur_per_mtok"] = DEFAULTS["price_eur_per_mtok"]
    if not isinstance(cfg.get("triggers"), list):
        cfg["triggers"] = []
    return cfg


def save_project_config(root: str, updates: dict) -> dict:
    store = ensure_store(project_store(root))
    path = os.path.join(store, "config.json")
    cfg = _read_json(path)
    cfg.update(updates)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    return cfg


def _scope_explicitly_set(root: str) -> bool:
    for path in (os.path.join(project_store(root), "config.json"),
                 os.path.join(global_store(), "..", "config.json")):
        if "scope" in _read_json(path):
            return True
    return "MEMORY_SCOPE" in os.environ


def read_stores(cfg: dict, root: str):
    if cfg["scope"] == "off":
        return []
    stores = []
    if cfg["scope"] in ("project", "both"):
        stores.append(("project", project_store(root)))
    if cfg.get("customer") and cfg["scope"] != "off":
        stores.append(("klant", customer_store(cfg["customer"])))
    include_global = cfg["scope"] in ("global", "both")
    if (not include_global and cfg["scope"] == "project"
            and not _scope_explicitly_set(root)):
        # niemand heeft ooit een scope gekozen: heeft de globale store inhoud,
        # dan doet hij gewoon mee -- opslaan-met-globaal en terugzoeken horen
        # dezelfde wereld te zien
        g = global_store()
        if os.path.isdir(os.path.join(g, "topics")) and list_topics(g):
            include_global = True
    if include_global:
        stores.append(("globaal", global_store()))
    return stores


def write_store(cfg: dict, root: str, override: str = None) -> str:
    choice = override or ("global" if cfg["scope"] == "global" else "project")
    if choice == "customer":
        if not cfg.get("customer"):
            choice = "project"  # geen klant ingesteld: veilig terugvallen
        else:
            return ensure_store(customer_store(cfg["customer"]))
    store = global_store() if choice == "global" else project_store(root)
    return ensure_store(store)


def log(store: str, msg: str) -> None:
    try:
        with open(os.path.join(store, ".log"), "a", encoding="utf-8") as f:
            stamp = datetime.datetime.now().isoformat(timespec="seconds")
            f.write(f"{stamp} {msg}\n")
    except OSError:
        pass


# --------------------------------------------------------------- triggers ---

DEFAULT_TRIGGERS = [
    (r"(?i)\b(besluit|beslissing|we kiezen|gekozen voor|decision)\b", "decisions"),
    (r"(?i)\b(vanaf nu|voortaan|from now on|in plaats van|instead of)\b", "decisions"),
    (r"(?i)\b(conventie|afspraak|stijlregel|convention|altijd .{3,40} gebruiken|never use|nooit .{3,40} gebruiken)\b", "conventions"),
    (r"(?i)\b(onthoud|remember this|let op|valkuil|gotcha|bekende bug|known issue)\b", "gotchas"),
    (r"(?i)\b(niet in scope|buiten scope|out of scope|bewust niet|expliciet geen|wil geen|komt er niet)\b", "scope-nee"),
    (r"(?i)\blaten we (dan )?(maar )?\w+([ \w]{0,25})? (nemen|gebruiken|kiezen)\b", "decisions"),
    (r"(?i)\bdan doen we het met\b", "decisions"),
]


def get_triggers(cfg: dict):
    """Default-triggers plus geldige eigen triggers uit de config.

    Config-formaat: "triggers": [{"pattern": "(?i)\\bregex\\b", "topic": "naam"}]
    """
    triggers = list(DEFAULT_TRIGGERS)
    for t in cfg.get("triggers", []):
        if not isinstance(t, dict):
            continue
        pattern, topic = t.get("pattern"), t.get("topic", "context")
        if not pattern:
            continue
        try:
            re.compile(pattern)
        except re.error:
            continue
        triggers.append((pattern, str(topic)))
    return triggers


def classify(sentence: str, triggers) -> str:
    for pattern, topic in triggers:
        if re.search(pattern, sentence):
            return topic
    return ""


def title_from(sentence: str) -> str:
    words = sentence.split()
    return " ".join(words[:9]) + ("..." if len(words) > 9 else "")


# ------------------------------------------------- integriteit van opslag ---

@contextmanager
def store_lock(store: str):
    """Exclusieve lock per store rond lees-wijzig-schrijf, tegen verloren
    updates bij gelijktijdige schrijvers (twee sessies, hook + handmatige
    save). fcntl.flock geeft de lock automatisch vrij bij een crash."""
    os.makedirs(store, exist_ok=True)
    f = open(os.path.join(store, ".lock"), "w")
    try:
        try:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX)
        except ImportError:
            pass  # niet-POSIX: geen lock, gedrag als voorheen
        yield
    finally:
        try:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_UN)
        except ImportError:
            pass
        f.close()


def _atomic_write(path: str, content: str) -> None:
    """Schrijf via tempbestand + rename: een crash laat nooit een half
    bestand achter (hetzelfde mechanisme dat databases zelf gebruiken)."""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


_HEADER_LIKE = re.compile(r"^(## \d{4}-\d{2}-\d{2} \|)", re.MULTILINE)


def _sanitize_body(body: str) -> str:
    """Voorkom dat een body het entry-formaat zelf kan naspelen."""
    body = _HEADER_LIKE.sub(r"·\1", body)
    first = body.split("\n", 1)[0]
    if re.match(r"^(keywords|refs|vervangt):", first):
        body = "· " + body
    return body


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


def parse_text(text: str, topic: str):
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


def parse_entries(path: str, topic: str):
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return parse_text(f.read(), topic)


def serialize_entries(entries: list) -> str:
    return "".join(format_entry(e) for e in entries)


def write_validated(path: str, topic: str, entries: list) -> bool:
    """Round-trip-check voor het schrijven: serialiseer, parse terug en
    vergelijk. Zou de parser ook maar 1 entry verliezen, dan wordt er NIET
    geschreven. Stil dataverlies is daarmee technisch onmogelijk."""
    if not entries:
        if os.path.isfile(path):
            os.remove(path)
        return True
    content = serialize_entries(entries)
    reparsed = parse_text(content, topic)
    if [entry_fingerprint(e) for e in reparsed] != \
            [entry_fingerprint(e) for e in entries]:
        return False
    _atomic_write(path, content)
    return True


def _doc_entry(path: str, topic: str, text: str) -> dict:
    """Een vrij document (gewone .md zonder entry-opmaak) gedraagt zich als
    1 entry: doorzoekbaar als geheel, met het pad erbij zodat Claude het
    volledige bestand zelf kan lezen (pull-model)."""
    title = topic
    m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    if m:
        title = m.group(1).strip()[:120]
    mtime = datetime.date.fromtimestamp(os.path.getmtime(path)).isoformat()
    return {
        "topic": topic, "date": mtime, "title": title,
        "keywords": extract_keywords(text, limit=12),
        "refs": [], "supersedes": "", "body": text,
        "doc": True, "path": path,
    }


def topic_entries(store: str, topic: str):
    path = topic_path(store, topic)
    entries = parse_entries(path, topic)
    if entries:
        return entries
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        if text.strip():
            return [_doc_entry(path, topic, text)]
    return []


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


def write_topic(store: str, topic: str, entries: list) -> bool:
    ok = write_validated(topic_path(store, topic), topic, entries)
    if not ok:
        log(store, f"write_topic GEWEIGERD (round-trip faalde): {topic}")
    return ok


def archive_entries(store: str, topic: str, entries: list, note: str = "") -> bool:
    if not entries:
        return True
    path = os.path.join(store, "archive", f"{slug(topic)}.md")
    existing = parse_entries(path, topic)
    for e in entries:
        e = dict(e)
        if note:
            e["body"] = e["body"].rstrip() + f"\n{note}"
        existing.append(e)
    ok = write_validated(path, topic, existing)
    if not ok:
        log(store, f"archive GEWEIGERD (round-trip faalde): {topic}")
    return ok


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


def _semantic_conflicts(store: str, topic: str, candidate: dict, cfg: dict):
    """Conflicten via cosine similarity, voor als keyword-overlap te laag is."""
    entries = topic_entries(store, topic)
    if not entries or not cfg:
        return []
    try:
        import embeddings
        emb = embeddings.get_embedder(cfg, store)
        # hash-vectoren scoren structureel lager dan modelvectoren (bigram-
        # penalty); bijna-identiek is daar ~0.6, echt verschillend ~0.15
        threshold = cfg["conflict_threshold"]
        if emb.name.startswith("hash"):
            threshold = min(threshold, 0.5)
        texts = [f"{e['title']}\n{e['body']}" for e in entries]
        texts.append(f"{candidate['title']}\n{candidate['body']}")
        vecs = emb.embed_batch(texts)
        cand = vecs[-1]
        is_hash = emb.name.startswith("hash")
        cand_kw = {stem(k) for k in candidate["keywords"]}
        out = []
        for e, v in zip(entries, vecs[:-1]):
            if len(v) != len(cand):
                continue
            sim = sum(a * b for a, b in zip(cand, v))
            hit = sim >= threshold
            if is_hash and not hit:
                # gemeten combinatieregel: matige similarity + gedeeld trefwoord
                overlap = len(cand_kw & {stem(k) for k in e["keywords"]})
                hit = sim >= 0.25 and overlap >= 1
            if hit:
                out.append(e)
        return out
    except Exception:
        return []


def append_entry(store: str, topic: str, title: str, keywords, body: str,
                 refs=None, cfg=None) -> dict:
    """Publieke, gelockte variant: de hele lees-wijzig-schrijf-cyclus draait
    exclusief per store, tegen verloren updates bij gelijktijdige schrijvers."""
    with store_lock(store):
        return _append_entry_unlocked(store, topic, title, keywords, body,
                                      refs=refs, cfg=cfg)


def _append_entry_unlocked(store: str, topic: str, title: str, keywords, body: str,
                 refs=None, cfg=None) -> dict:
    """Voeg toe met scrubbing, dedupe en conflictdetectie.

    Resultaat: {added, reason, redacted, superseded:[titels]}
    """
    ensure_store(store)
    if len(body) > 6000:
        return {"added": False, "reason": "body is te groot voor een entry "
                "(~>1500 tokens); zet het als geheel bestand in topics/ "
                "(vrij document, automatisch doorzoekbaar) of gebruik "
                "/project-memory:memory-import om het te destilleren",
                "redacted": 0, "superseded": []}
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
        "body": _sanitize_body(body_clean.strip()),
    }
    if entry_fingerprint(candidate) in {entry_fingerprint(e) for e in all_entries(store)}:
        return {"added": False, "reason": "duplicaat", "redacted": redacted,
                "superseded": []}

    conflicts = _find_conflicts(store, topic, candidate)
    if not conflicts and cfg is not None and slug(topic) in CONFLICT_TOPICS:
        conflicts = _semantic_conflicts(store, topic, candidate, cfg)
    if conflicts:
        remaining = [e for e in topic_entries(store, topic)
                     if entry_fingerprint(e) not in {entry_fingerprint(c) for c in conflicts}]
        write_topic(store, topic, remaining)
        note = f"vervangen-door: {candidate['date']} | {candidate['title']}"
        archive_entries(store, topic, conflicts, note=note)
        newest = max(conflicts, key=lambda e: e["date"])
        candidate["supersedes"] = f"{newest['date']} | {newest['title']}"

    final = topic_entries(store, topic) + [candidate]
    if not write_validated(topic_path(store, topic), topic, final):
        log(store, f"append GEWEIGERD (round-trip faalde): {topic} | {candidate['title']}")
        return {"added": False, "reason": "integriteitscheck faalde; entry zou "
                "onparseerbaar zijn en is niet geschreven", "redacted": redacted,
                "superseded": []}
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


# -------------------------------------------------------------- validator ---

def validate_store(store: str) -> list:
    """Strikte schemacontrole. Retourneert een lijst bevindingen (leeg = ok)."""
    issues = []
    if not os.path.isdir(store):
        return issues
    for sub in ("topics", "archive"):
        d = os.path.join(store, sub)
        if not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d)):
            path = os.path.join(d, fname)
            if fname.startswith(".tmp-"):
                issues.append(f"{sub}/{fname}: achtergebleven tempbestand "
                              "(afgebroken schrijfactie)")
                continue
            if not fname.endswith(".md"):
                continue
            with open(path, encoding="utf-8") as f:
                text = f.read()
            topic = fname[:-3]
            entries = parse_text(text, topic)
            if text.strip() and not entries:
                continue  # vrij document: doorzoekbaar als geheel, geen fout
            if serialize_entries(entries).strip() != text.strip():
                issues.append(f"{sub}/{fname}: tekst buiten het entry-formaat "
                              "(handmatige edit of merge-restje)")
            seen = {}
            for e in entries:
                fp = entry_fingerprint(e)
                if fp in seen:
                    issues.append(f"{sub}/{fname}: duplicaat '{e['title']}'")
                seen[fp] = True
                if not e["keywords"]:
                    issues.append(f"{sub}/{fname}: '{e['title']}' heeft geen keywords")
    return issues


def cleanup_temp_files(store: str, max_age_s: int = 3600) -> int:
    removed = 0
    for sub in ("topics", "archive", ""):
        d = os.path.join(store, sub) if sub else store
        if not os.path.isdir(d):
            continue
        for fname in os.listdir(d):
            if fname.startswith(".tmp-"):
                p = os.path.join(d, fname)
                try:
                    import time as _t
                    if _t.time() - os.path.getmtime(p) > max_age_s:
                        os.unlink(p)
                        removed += 1
                except OSError:
                    pass
    return removed


# ------------------------------------------------------------------ usage ---

def record_usage(store: str, topic: str) -> None:
    try:
        with open(os.path.join(store, ".usage.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.datetime.now().isoformat(timespec="seconds"),
                                "topic": topic}) + "\n")
    except OSError:
        pass


def usage_counts(store: str, days: int = 90) -> dict:
    path = os.path.join(store, ".usage.jsonl")
    counts = {}
    if not os.path.isfile(path):
        return counts
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("ts", "") >= cutoff:
                    t = rec.get("topic", "")
                    counts[t] = counts.get(t, 0) + 1
    except OSError:
        pass
    return counts


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
        usage = usage_counts(store)
        for t in sorted(list_topics(store), key=lambda t: (-usage.get(t, 0), t)):
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


def stem(w: str) -> str:
    """Lichte NL/EN-suffixstripper zodat tekenen/tekening/daken/dak matchen."""
    for suf in ("ingen", "en", "ing", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


def stems(w: str) -> set:
    """Alle matchvarianten van een woord: normale stem, plus de stam zonder
    Nederlands ge-voorvoegsel (gehost -> host) als extra kandidaat."""
    out = {stem(w)}
    if w.startswith("ge") and len(w) > 5:
        out.add(stem(w[2:]))
    return out


def stem_text(text: str) -> str:
    """Tekst als gestemde token-stroom (alle matchvarianten), voor de
    FTS5/BM25-index; zo blijven dagplanning~planning en gehost~host werken."""
    out = []
    for w in re.findall(r"\w+", text.lower()):
        if w not in STOPWORDS and len(w) > 2:
            out.extend(sorted(stems(w)))
    return " ".join(out)


def _prompt_words(prompt: str) -> set:
    words = set()
    for w in re.findall(r"\w+", prompt.lower()):
        if w not in STOPWORDS and len(w) > 2:
            words |= stems(w)
    return words


def score_entry(entry: dict, prompt_words: set) -> int:
    score = 0
    for kw in entry["keywords"]:
        for part in re.findall(r"\w+", kw):
            p = stem(part)
            if p in prompt_words:
                score += 3
            elif len(p) >= 5:
                # NL samenstellingen: dagplanning ~ planning, daktekening ~ tekening
                if any(len(w) >= 5 and (p in w or w in p) for w in prompt_words):
                    score += 3
    for w in re.findall(r"\w+", entry["title"].lower()):
        if w not in STOPWORDS and stem(w) in prompt_words:
            score += 3 if len(w) >= 5 else 1
    body_words = set()
    for w in re.findall(r"\w+", entry["body"].lower()):
        if w not in STOPWORDS:
            body_words |= stems(w)
    overlap = len(body_words & prompt_words)
    if entry.get("doc"):
        # vrij document (door de gebruiker neergezet): 1 sterk woord is een
        # zinvolle verwijzing en telt als keywordmatch
        score += max(3, overlap) if overlap else 0
    else:
        score += overlap // 3
    return score


def effective_semantic_threshold(cfg: dict, backend_name: str) -> float:
    """Hash-vectoren scoren structureel lager dan modelvectoren; gemeten
    optimum voor hint-injectie met hash is 0.12."""
    th = cfg["semantic_threshold"]
    return min(th, 0.12) if backend_name.startswith("hash") else th


def matching_topics(cfg: dict, root: str, prompt: str):
    pw = _prompt_words(prompt)
    if not pw:
        return []
    hits = []
    for label, store in read_stores(cfg, root):
        for t in list_topics(store):
            score = sum(score_entry(e, pw) for e in topic_entries(store, t))
            if score >= 3:
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
        cost = 400 if e.get("doc") else token_estimate(e["title"] + e["body"])
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
    p_add.add_argument("--store", choices=["project", "global", "customer"], default=None)

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
    p_cfg.add_argument("--customer")
    p_cfg.add_argument("--show", action="store_true")

    p_re = sub.add_parser("reindex")
    p_re.add_argument("--root", default=os.getcwd())

    p_adr = sub.add_parser("export-adr")
    p_adr.add_argument("--root", default=os.getcwd())

    p_val = sub.add_parser("validate")
    p_val.add_argument("--root", default=os.getcwd())

    args = parser.parse_args()
    root = find_project_root(args.root)
    cfg = load_config(root)

    if args.cmd == "add":
        store = write_store(cfg, root, override=args.store)
        keywords = args.keywords.split(",") if args.keywords else extract_keywords(args.body)
        refs = [r.strip() for r in args.refs.split(",") if r.strip()] \
            or extract_refs(args.body, root if store != global_store() else "")
        result = append_entry(store, args.topic, args.title, keywords, args.body, refs=refs, cfg=cfg)
        rebuild_index(cfg, root)
        try:
            import embeddings
            embeddings.sync(store, cfg)
        except Exception:
            pass
        if store == global_store():
            where = "globaal"
        elif cfg.get("customer") and store == customer_store(cfg["customer"]):
            where = "klant"
        else:
            where = "project"
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
                    "retrieval", "embedding_backend", "embedding_model", "customer")
                   if getattr(args, k, None) is not None}
        if updates:
            save_project_config(root, updates)
            cfg = load_config(root)
            rebuild_index(cfg, root)
        print(json.dumps(cfg, indent=2))
    elif args.cmd == "reindex":
        print(rebuild_index(cfg, root))
    elif args.cmd == "validate":
        total = 0
        for label, store in read_stores(cfg, root):
            issues = validate_store(store)
            total += len(issues)
            status = "OK" if not issues else f"{len(issues)} bevinding(en)"
            print(f"[{label}] {store}: {status}")
            for issue in issues:
                print(f"    {issue}")
        return 1 if total else 0
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
