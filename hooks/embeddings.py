#!/usr/bin/env python3
"""Semantische index voor project-memory (v0.4).

Architectuur: de Markdown in topics/ blijft de bron van waarheid. Dit module
onderhoudt per store een herbouwbare SQLite-cache (.index.db) met metadata en
embedding-vectoren, en zoekt daarin met brute-force cosine similarity. Op de
schaal van memory (honderden tot duizenden entries) is dat milliseconden werk;
een aparte vector-database voegt hier alleen gewicht toe.

Backends (config "embedding_backend", env MEMORY_EMBEDDING_BACKEND):
  hash    feature-hashing van woorden en bigrammen, 256-dim, pure stdlib.
          Geen dependencies, geen netwerk, deterministisch. Lexicaal, dus
          een fallback: geen echte synoniemen, wel robuuste overlap-matching.
  local   sentence-transformers (pip install sentence-transformers);
          default model all-MiniLM-L6-v2, draait volledig lokaal.
  voyage  Voyage AI API (env VOYAGE_API_KEY), default voyage-3.5-lite.
  openai  OpenAI API (env OPENAI_API_KEY), default text-embedding-3-small.

Bij een backend die niet beschikbaar is (library ontbreekt, geen API-key,
netwerkfout) valt alles terug op hash, met een logregel. De hooks blokkeren
nooit op een externe dienst.
"""

import hashlib
import json
import math
import os
import re
import sqlite3
import struct
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memlib  # noqa: E402

HASH_DIM = 256


# --------------------------------------------------------------- backends ---

class HashEmbedder:
    name = "hash-v2"
    dim = HASH_DIM

    def _tokens(self, text: str):
        words = [memlib.stem(w) for w in re.findall(r"\w+", text.lower())
                 if len(w) > 2 and w not in memlib.STOPWORDS]
        return words + [f"{a}_{b}" for a, b in zip(words, words[1:])]

    def embed_batch(self, texts):
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for tok in self._tokens(text):
                h = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
                idx = int.from_bytes(h[:4], "big") % self.dim
                sign = 1.0 if h[4] % 2 else -1.0
                vec[idx] += sign
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


class LocalEmbedder:
    def __init__(self, model: str):
        from sentence_transformers import SentenceTransformer  # lazy
        self.model_name = model or "all-MiniLM-L6-v2"
        self.name = f"local:{self.model_name}"
        self._m = SentenceTransformer(self.model_name)
        self.dim = self._m.get_sentence_embedding_dimension()

    def embed_batch(self, texts):
        return [list(map(float, v)) for v in
                self._m.encode(texts, normalize_embeddings=True)]


class _ApiEmbedder:
    url = ""
    key_env = ""
    default_model = ""

    def __init__(self, model: str):
        self.model_name = model or self.default_model
        self.name = f"{self.key_env.split('_')[0].lower()}:{self.model_name}"
        self.key = os.environ.get(self.key_env, "")
        if not self.key:
            raise RuntimeError(f"{self.key_env} niet gezet")
        self.dim = 0  # bekend na eerste call

    def embed_batch(self, texts):
        req = urllib.request.Request(
            self.url,
            data=json.dumps({"model": self.model_name, "input": texts}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.key}"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
        vecs = [d["embedding"] for d in data["data"]]
        out = []
        for v in vecs:
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        self.dim = len(out[0]) if out else 0
        return out


class VoyageEmbedder(_ApiEmbedder):
    url = "https://api.voyageai.com/v1/embeddings"
    key_env = "VOYAGE_API_KEY"
    default_model = "voyage-3.5-lite"


class OpenAIEmbedder(_ApiEmbedder):
    url = "https://api.openai.com/v1/embeddings"
    key_env = "OPENAI_API_KEY"
    default_model = "text-embedding-3-small"


def get_embedder(cfg: dict, store: str = ""):
    backend = str(cfg.get("embedding_backend", "hash"))
    model = str(cfg.get("embedding_model", "") or "")
    try:
        if backend == "local":
            return LocalEmbedder(model)
        if backend == "voyage":
            return VoyageEmbedder(model)
        if backend == "openai":
            return OpenAIEmbedder(model)
    except Exception as exc:  # library mist, key mist, netwerk: terugvallen
        if store:
            memlib.log(store, f"embeddings: backend {backend} onbeschikbaar "
                              f"({exc}); terugvallen op hash")
    return HashEmbedder()


# --------------------------------------------------------------- database ---

def db_path(store: str) -> str:
    return os.path.join(store, ".index.db")


def _connect(store: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path(store), timeout=5)
    con.execute("""CREATE TABLE IF NOT EXISTS vectors(
        fingerprint TEXT PRIMARY KEY,
        topic TEXT, date TEXT, title TEXT, body TEXT,
        model TEXT, dim INTEGER, vector BLOB, updated REAL)""")
    return con


def _pack(vec) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack(blob: bytes, dim: int):
    return struct.unpack(f"{dim}f", blob)


def _ensure_gitignore(store: str) -> None:
    path = os.path.join(store, ".gitignore")
    wanted = {".index.db", ".lock", ".log", ".last_consolidation", ".usage.jsonl", "report.html"}
    have = set()
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            have = {l.strip() for l in f if l.strip()}
    missing = wanted - have
    if missing:
        with open(path, "a", encoding="utf-8") as f:
            for m in sorted(missing):
                f.write(m + "\n")


def sync(store: str, cfg: dict) -> dict:
    """Breng .index.db in lijn met de Markdown. Herbouwbaar en incrementeel."""
    if not os.path.isdir(store):
        return {"embedded": 0, "removed": 0}
    _ensure_gitignore(store)
    embedder = get_embedder(cfg, store)
    entries = {memlib.entry_fingerprint(e): e for e in memlib.all_entries(store)}

    con = _connect(store)
    try:
        rows = con.execute("SELECT fingerprint, model FROM vectors").fetchall()
        known = {fp for fp, model in rows if model == embedder.name}
        stale = [fp for fp, model in rows
                 if fp not in entries or model != embedder.name]
        if stale:
            con.executemany("DELETE FROM vectors WHERE fingerprint=?",
                            [(fp,) for fp in stale])

        todo = [(fp, e) for fp, e in entries.items() if fp not in known]
        if todo:
            texts = [f"{e['title']}\n{e['body']}" for _fp, e in todo]
            vecs = embedder.embed_batch(texts)
            now = time.time()
            con.executemany(
                "INSERT OR REPLACE INTO vectors VALUES(?,?,?,?,?,?,?,?,?)",
                [(fp, e["topic"], e["date"], e["title"], e["body"],
                  embedder.name, len(v), _pack(v), now)
                 for (fp, e), v in zip(todo, vecs)])
        con.commit()
        return {"embedded": len(todo), "removed": len(stale),
                "backend": embedder.name}
    finally:
        con.close()


def search(store: str, cfg: dict, query: str, top_k: int = 10):
    """[(score, entry-dict)] gesorteerd op cosine similarity."""
    if not os.path.isfile(db_path(store)):
        return []
    embedder = get_embedder(cfg, store)
    qvec = embedder.embed_batch([query])[0]

    con = _connect(store)
    try:
        rows = con.execute(
            "SELECT topic, date, title, body, dim, vector FROM vectors "
            "WHERE model=?", (embedder.name,)).fetchall()
    finally:
        con.close()

    scored = []
    for topic, date, title, body, dim, blob in rows:
        if dim != len(qvec):
            continue
        vec = _unpack(blob, dim)
        score = sum(a * b for a, b in zip(qvec, vec))
        scored.append((score, {"topic": topic, "date": date,
                               "title": title, "body": body}))
    scored.sort(key=lambda p: -p[0])
    return scored[:top_k]


def topic_scores(store: str, cfg: dict, query: str) -> dict:
    """Beste entry-score per topic, voor hint-injectie."""
    best = {}
    for score, e in search(store, cfg, query, top_k=50):
        if score > best.get(e["topic"], 0.0):
            best[e["topic"]] = score
    return best


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["sync", "search"])
    parser.add_argument("--root", default=os.getcwd())
    parser.add_argument("--query", default="")
    args = parser.parse_args()

    root = memlib.find_project_root(args.root)
    cfg = memlib.load_config(root)
    for label, store in memlib.read_stores(cfg, root):
        if args.action == "sync":
            stats = sync(store, cfg)
            print(f"[{label}] {stats}")
        else:
            for score, e in search(store, cfg, args.query, top_k=5):
                print(f"[{label}] {score:.3f}  {e['topic']}: {e['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
