#!/usr/bin/env python3
"""Stop: triageer de sessie en bewaar wat bewaarwaard is (deterministisch,
pure stdlib). Schrijft naar de write-store die uit de scope-config volgt en
draait hooguit 1x per 24 uur de consolidatie."""

import datetime
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memlib  # noqa: E402

TRIGGERS = [
    (r"(?i)\b(besluit|beslissing|we kiezen|gekozen voor|decision)\b", "decisions"),
    (r"(?i)\b(vanaf nu|voortaan|from now on|in plaats van|instead of)\b", "decisions"),
    (r"(?i)\b(conventie|afspraak|stijlregel|convention|altijd .{3,40} gebruiken|never use|nooit .{3,40} gebruiken)\b", "conventions"),
    (r"(?i)\b(onthoud|remember this|let op|valkuil|gotcha|bekende bug|known issue|pas op)\b", "gotchas"),
]

MAX_SENTENCE_LEN = 300
MAX_NEW_PER_SESSION = 8


def transcript_texts(path: str):
    if not path or not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            role = obj.get("type", "")
            msg = obj.get("message") or {}
            content = msg.get("content", "")
            if isinstance(content, str):
                if content.strip():
                    yield role, content
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        if text.strip():
                            yield role, text


def sentences(text: str):
    for s in re.split(r"(?<=[.!?])\s+|\n+", text):
        s = s.strip()
        if 15 <= len(s) <= MAX_SENTENCE_LEN:
            yield s


def triage(path: str):
    seen, found = set(), []
    for role, text in transcript_texts(path):
        for s in sentences(text):
            key = re.sub(r"\W+", " ", s.lower()).strip()
            if key in seen:
                continue
            for pattern, topic in TRIGGERS:
                if re.search(pattern, s):
                    seen.add(key)
                    found.append((topic, s, role))
                    break
    found.sort(key=lambda t: 0 if t[2] == "user" else 1)
    return found[:MAX_NEW_PER_SESSION]


def title_from(sentence: str) -> str:
    words = sentence.split()
    return " ".join(words[:9]) + ("..." if len(words) > 9 else "")


def maybe_consolidate(store: str, root: str) -> None:
    marker = os.path.join(store, ".last_consolidation")
    now = datetime.datetime.now().timestamp()
    if os.path.isfile(marker) and now - os.path.getmtime(marker) < 24 * 3600:
        return
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "consolidate.py")
    try:
        subprocess.run([sys.executable, script, "--root", root],
                       timeout=20, capture_output=True)
    except (subprocess.TimeoutExpired, OSError) as exc:
        memlib.log(store, f"stop_triage: consolidatie mislukt: {exc}")


def main() -> int:
    data = memlib.read_hook_input()
    if data.get("stop_hook_active"):
        return 0

    root = memlib.find_project_root(data.get("cwd", os.getcwd()))
    cfg = memlib.load_config(root)
    if cfg["scope"] == "off":
        return 0

    store = memlib.write_store(cfg, root)
    saved, redacted = 0, 0
    for topic, sentence, _role in triage(data.get("transcript_path", "")):
        keywords = memlib.extract_keywords(sentence)
        refs = memlib.extract_refs(sentence, root)
        result = memlib.append_entry(store, topic, title_from(sentence),
                                     keywords, sentence, refs=refs)
        if result["added"]:
            saved += 1
            redacted += result["redacted"]

    if saved:
        memlib.rebuild_index(cfg, root)
        try:
            import embeddings
            embeddings.sync(store, cfg)
        except Exception as exc:
            memlib.log(store, f"stop_triage: embedding-sync mislukt: {exc}")
        memlib.log(store, f"stop_triage: {saved} nieuwe entries, {redacted} geheimen geredigeerd")
    maybe_consolidate(store, root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
