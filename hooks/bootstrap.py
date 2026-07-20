#!/usr/bin/env python3
"""Bootstrap: mijn de git-historie en artefacten van een bestaand project en
bereid ze voor als destillatiemateriaal, zodat /memory-bootstrap er een
start-memory uit kan opbouwen voor projecten die nooit een kickoff hadden.

Bronnen, allemaal deterministisch verzameld:
  1. commit-messages (merges en triviale commits eruit; commits met een body
     of besluit-achtige woorden krijgen voorrang, want daar zit de rationale)
  2. TODO/FIXME/HACK/XXX-comments met bestand en regelnummer (worden gotchas,
     en het bestandspad gaat mee als ref zodat de verificatie erop werkt)
  3. de kop van de README (projectcontext)
  4. tags (release-geschiedenis)

Uitvoer: chunks in .claude/memory/imports/bootstrap-*.txt (gitignored),
klaar om door Claude gelezen en gedestilleerd te worden.

Gebruik: python3 bootstrap.py [--root <projectpad>] [--max-commits 120]
"""

import argparse
import os
import re
import signal
import subprocess
import sys

try:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except (AttributeError, ValueError):
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memlib  # noqa: E402
from import_prep import chunk_text  # noqa: E402

SEP = "\x1e"
TRIVIAL = re.compile(
    r"(?i)^(wip|typo|typos|lint|format|formatting|cleanup|clean up|bump|"
    r"merge|revert|fix typo|whitespace|comments?|readme|version|release|"
    r"initial commit|first commit|update)\b")
DECISION_HINT = re.compile(
    r"(?i)\b(switch|migrat|refactor|introduc|upgrad|replac|remove|drop|"
    r"vervang|verhuis|kies|gekozen|in plaats van|instead of|van \w+ naar|"
    r"from \w+ to|add(ed)? \w+ support|disable|enable|config|standaard|"
    r"default|security|performance)\b")


def git(root, *args):
    result = subprocess.run(["git", "-C", root, *args],
                            capture_output=True, text=True, timeout=60)
    return result.stdout if result.returncode == 0 else ""


def mine_commits(root: str, max_commits: int):
    raw = git(root, "log", "--no-merges", "--date=short",
              f"--pretty=format:%h|%ad|%s|%b{SEP}")
    scored = []
    for block in raw.split(SEP):
        block = block.strip()
        if not block:
            continue
        parts = block.split("|", 3)
        if len(parts) < 3:
            continue
        h, date, subject = parts[0], parts[1], parts[2].strip()
        body = parts[3].strip() if len(parts) > 3 else ""
        if len(subject) < 12 or TRIVIAL.match(subject):
            continue
        score = 0
        if body:
            score += 2  # een body betekent meestal: hier staat de waarom
        if DECISION_HINT.search(subject + " " + body):
            score += 2
        scored.append((score, date, h, subject, body))
    scored.sort(key=lambda c: (-c[0], c[1]))
    picked = sorted(scored[:max_commits], key=lambda c: c[1])  # chronologisch
    lines = []
    for _s, date, h, subject, body in picked:
        line = f"{date} [{h}] {subject}"
        if body:
            line += "\n    " + body[:400].replace("\n", "\n    ")
        lines.append(line)
    return lines, len(scored)


def mine_todos(root: str, limit: int = 80):
    raw = git(root, "grep", "-n", "-I", "-E", r"(TODO|FIXME|HACK|XXX)[:\s]")
    out = []
    for line in raw.splitlines():
        if ".claude/memory" in line or len(line) > 300:
            continue
        out.append(line.strip())
        if len(out) >= limit:
            break
    return out


def mine_readme(root: str, max_lines: int = 120):
    for name in ("README.md", "README.rst", "README.txt", "readme.md"):
        path = os.path.join(root, name)
        if os.path.isfile(path):
            with open(path, encoding="utf-8", errors="replace") as f:
                return [l.rstrip() for l in f.readlines()[:max_lines]]
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=os.getcwd())
    parser.add_argument("--max-commits", type=int, default=120)
    args = parser.parse_args()
    root = memlib.find_project_root(args.root)
    if not os.path.isdir(os.path.join(root, ".git")):
        print("geen git-repo gevonden; bootstrap heeft historie nodig")
        return 1

    commits, total = mine_commits(root, args.max_commits)
    todos = mine_todos(root)
    readme = mine_readme(root)
    tags = [t for t in git(root, "tag", "--sort=creatordate").splitlines()][-15:]

    sections = []
    if readme:
        sections.append("=== README (projectcontext) ===\n" + "\n".join(readme))
    if commits:
        sections.append(f"=== COMMITS ({len(commits)} van {total} relevante, "
                        "chronologisch; regels met inspring zijn de commit-body "
                        "met de rationale) ===\n" + "\n".join(commits))
    if todos:
        sections.append("=== TODO/FIXME/HACK (kandidaat-gotchas; formaat "
                        "bestand:regel:tekst, neem het bestand mee als ref) ===\n"
                        + "\n".join(todos))
    if tags:
        sections.append("=== TAGS (release-geschiedenis) ===\n" + "\n".join(tags))
    if not sections:
        print("niets bruikbaars gevonden in deze repo")
        return 1

    chunks = chunk_text([("", "\n\n".join(sections))])
    out_dir = os.path.join(memlib.ensure_store(memlib.project_store(root)), "imports")
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for i, chunk in enumerate(chunks, start=1):
        suffix = f"-{i:02d}" if len(chunks) > 1 else ""
        p = os.path.join(out_dir, f"bootstrap{suffix}.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(chunk)
        paths.append(p)

    total_tokens = sum(memlib.token_estimate(c) for c in chunks)
    print(f"bronnen: {len(commits)} commits, {len(todos)} TODO/FIXME's, "
          f"README {'ja' if readme else 'nee'}, {len(tags)} tags")
    print(f"destillatiemateriaal: ~{total_tokens} tokens in {len(paths)} chunk(s):")
    for p in paths:
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
