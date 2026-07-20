#!/usr/bin/env python3
"""Tijdmachine: reconstrueer de projectmemory zoals die op een moment in de
git-historie was, en toon wat er sindsdien is bijgekomen of verdwenen.

Werkt omdat de projectmemory in de repo leeft: 'wat wisten we toen' is
letterlijk 'git show <commit>:.claude/memory/topics/...'.

Gebruik:
  python3 asof.py <ref-of-datum> [--root <pad>]

<ref-of-datum> mag zijn: een commit/tag/branch, of een datum (2026-03 of
2026-03-15); bij een datum pakt hij de laatste commit voor dat moment.

Uitvoer: een snapshot in .claude/memory/asof/<ref>/ (gitignored) plus een
vergelijking op entry-niveau met nu.
"""

import os
import signal

try:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except (AttributeError, ValueError):
    pass

import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memlib  # noqa: E402


def git(root, *args, check=True):
    result = subprocess.run(["git", "-C", root, *args],
                            capture_output=True, text=True, timeout=30)
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} faalde")
    return result.stdout


def resolve_ref(root: str, spec: str) -> str:
    if re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", spec):
        until = spec if len(spec) == 10 else f"{spec}-01"
        # datum: laatste commit voor (of op) dat moment; bij YYYY-MM einde v/d maand
        if len(spec) == 7:
            until = f"{spec}-31"
        commit = git(root, "rev-list", "-1", f"--before={until} 23:59", "HEAD").strip()
        if not commit:
            raise RuntimeError(f"geen commits gevonden voor {spec}")
        return commit
    return git(root, "rev-parse", "--verify", f"{spec}^{{commit}}").strip()


def entries_at(root: str, commit: str):
    """Alle memory-entries zoals ze in de repo stonden bij <commit>."""
    prefixes = (".claude/memory/topics/", ".claude/memory/")
    try:
        files = git(root, "ls-tree", "-r", "--name-only", commit,
                    "--", ".claude/memory").splitlines()
    except RuntimeError:
        return {}, {}
    entries, contents = {}, {}
    for f in files:
        if not f.endswith(".md") or "/archive/" in f or f.endswith("index.md"):
            continue
        if not f.startswith(prefixes[0]) and os.path.dirname(f) + "/" != prefixes[1]:
            continue
        topic = os.path.basename(f)[:-3]
        text = git(root, "show", f"{commit}:{f}", check=False)
        if not text:
            continue
        contents[topic] = text
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                         encoding="utf-8") as tmp:
            tmp.write(text)
            tmppath = tmp.name
        try:
            for e in memlib.parse_entries(tmppath, topic):
                entries[memlib.entry_fingerprint(e)] = e
        finally:
            os.unlink(tmppath)
    return entries, contents


def main() -> int:
    if len(sys.argv) < 2:
        print("gebruik: asof.py <ref-of-datum> [--root <pad>]")
        return 1
    spec = sys.argv[1]
    root = memlib.find_project_root(
        sys.argv[sys.argv.index("--root") + 1] if "--root" in sys.argv else os.getcwd())
    if not os.path.isdir(os.path.join(root, ".git")):
        print("geen git-repo gevonden; de tijdmachine heeft git-historie nodig")
        return 1

    try:
        commit = resolve_ref(root, spec)
    except RuntimeError as exc:
        print(f"kan '{spec}' niet herleiden: {exc}")
        return 1

    when = git(root, "show", "-s", "--format=%ci %s", commit).strip()
    then_entries, contents = entries_at(root, commit)

    store = memlib.project_store(root)
    now_entries = {memlib.entry_fingerprint(e): e for e in memlib.all_entries(store)}

    # snapshot wegschrijven zodat Claude de details kan lezen
    safe = re.sub(r"[^\w\-]", "-", spec)[:30]
    snap_dir = os.path.join(store, "asof", safe)
    os.makedirs(snap_dir, exist_ok=True)
    gi = os.path.join(store, ".gitignore")
    have = set()
    if os.path.isfile(gi):
        with open(gi, encoding="utf-8") as f:
            have = {l.strip() for l in f}
    if "asof/" not in have:
        with open(gi, "a", encoding="utf-8") as f:
            f.write("asof/\n")
    for topic, text in contents.items():
        with open(os.path.join(snap_dir, f"{topic}.md"), "w", encoding="utf-8") as f:
            f.write(text)

    added = [e for fp, e in now_entries.items() if fp not in then_entries]
    removed = [e for fp, e in then_entries.items() if fp not in now_entries]

    print(f"peilmoment: {commit[:10]} ({when})")
    print(f"memory toen: {len(then_entries)} entries | nu: {len(now_entries)} entries")
    print(f"snapshot: {snap_dir}/")
    if then_entries:
        print("\nWat het team TOEN wist:")
        for e in sorted(then_entries.values(), key=lambda e: (e["topic"], e["date"])):
            print(f"  [{e['topic']}] {e['date']} | {e['title']}")
    else:
        print("\nOp dat moment bestond er nog geen memory in de repo.")
    if added:
        print("\nPas SINDSDIEN geleerd (bestond toen dus niet):")
        for e in sorted(added, key=lambda e: e["date"]):
            print(f"  [{e['topic']}] {e['date']} | {e['title']}")
    if removed:
        print("\nToen wel, nu niet meer actief (gearchiveerd of vervangen):")
        for e in removed:
            print(f"  [{e['topic']}] {e['date']} | {e['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
