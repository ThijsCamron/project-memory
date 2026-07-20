#!/usr/bin/env python3
"""Voorbewerking voor /memory-import: maak een bronbestand schoon en compact
zodat Claude het efficient kan lezen en destilleren.

Ondersteunde formaten:
  .json / .jsonl   gesprekstranscripten ({"sentence","speaker_name"} per item)
  .vtt / .srt      ondertitel-transcripten (Teams/Zoom-export): timestamps
                   eruit, rollende herhalingen gededupliceerd, sprekers uit
                   <v Naam>-tags of "Naam: tekst"-regels
  .docx            Word: tekst uit word/document.xml (pure stdlib, geen deps)
  .pdf             via pdftotext (poppler) of pypdf indien aanwezig; anders
                   duidelijke melding dat Claude het PDF direct met Read leest
  .eml             e-mail: headers plus beste tekstdeel (stdlib email)
  .html / .htm     tekst zonder tags, scripts en styles (stdlib)
  .md / .txt       gaan vrijwel ongewijzigd door

Uitvoer: schone tekstbestanden in <project>/.claude/memory/imports/
(automatisch gitignored). Grote bronnen worden gesplitst in chunks van
~CHUNK_TOKENS zodat elke Read betaalbaar blijft.

Gebruik: python3 import_prep.py <bronbestand> [--root <projectpad>]
"""

import html
import json
import signal

try:  # nette exit als output door head/less wordt afgekapt
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except (AttributeError, ValueError):
    pass
import os
import re
import shutil
import subprocess
import sys
import zipfile
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memlib  # noqa: E402

CHUNK_TOKENS = 8000

FILLERS = {
    "ja", "nee", "oke", "ok", "oh", "precies", "exact", "top", "mooi", "klopt",
    "zeker", "inderdaad", "goed", "prima", "duidelijk", "ja precies", "oh ja",
    "ja ja", "nee nee", "is goed", "dat is zo", "ja klopt", "ja exact",
    "ja duidelijk", "nee maar ja is wel zo", "heel gaaf ja", "ja mooi",
}


def _norm(s: str) -> str:
    return re.sub(r"[^\w ]", "", s.lower()).strip()


# ---------------------------------------------------------- formaat-lezers ---

def read_json_transcript(raw: str):
    data = json.loads(raw)
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        s = str(item.get("sentence") or item.get("text") or "").strip()
        if s:
            spk = str(item.get("speaker_name") or item.get("speaker_id") or "spreker")
            out.append((spk, s))
    return out


def read_jsonl_transcript(raw: str):
    out = []
    for line in raw.splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        s = str(obj.get("sentence") or obj.get("text") or "").strip()
        if s:
            out.append((str(obj.get("speaker_name") or "spreker"), s))
    return out


def read_subtitles(raw: str):
    """VTT/SRT: timestamps en volgnummers eruit, rollende duplicaten weg."""
    out, prev = [], ""
    for line in raw.splitlines():
        line = line.strip()
        if (not line or line == "WEBVTT" or line.isdigit()
                or "-->" in line or line.startswith(("NOTE", "STYLE", "REGION"))):
            continue
        m = re.match(r"<v\s+([^>]+)>(.*?)(?:</v>)?$", line)
        if m:
            spk, text = m.group(1).strip(), m.group(2).strip()
        else:
            m2 = re.match(r"([A-Z][\w .\-]{1,30}):\s+(.*)$", line)
            spk, text = (m2.group(1), m2.group(2)) if m2 else ("spreker", line)
        text = re.sub(r"</?[a-z][^>]*>", "", text).strip()
        if not text:
            continue
        n = _norm(text)
        if n and prev:
            if n == prev or n in prev:
                continue  # herhaling of ingekorte echo: overslaan
            if prev in n and out:
                out[-1] = (spk, text)  # rollende regel groeit: vervang door de langere
                prev = n
                continue
        prev = n
        out.append((spk, text))
    return out


def read_docx(path: str) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    xml = re.sub(r"</w:p>", "\n", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    return html.unescape(text)


def read_pdf(path: str) -> str:
    if shutil.which("pdftotext"):
        result = subprocess.run(["pdftotext", "-layout", path, "-"],
                                capture_output=True, timeout=60)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.decode("utf-8", errors="replace")
    try:
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
    except ImportError:
        pass
    return ""


def read_eml(raw_bytes: bytes) -> str:
    import email
    from email import policy
    msg = email.message_from_bytes(raw_bytes, policy=policy.default)
    header = "\n".join(f"{k}: {msg.get(k, '')}" for k in ("From", "To", "Date", "Subject"))
    body = ""
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is not None:
        content = part.get_content()
        if part.get_content_type() == "text/html":
            content = strip_html(content)
        body = content
    return f"{header}\n\n{body}"


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def strip_html(raw: str) -> str:
    p = _TextExtractor()
    p.feed(raw)
    return re.sub(r"\n{3,}", "\n\n", "".join(p.parts))


# ------------------------------------------------------------- pipeline ---

def parse_source(path: str):
    """([(spreker, tekst)], formaatnaam). Sprekerloos = ("", tekst)-regels."""
    ext = os.path.splitext(path)[1].lower()

    if ext == ".docx":
        return [("", read_docx(path))], "docx"
    if ext == ".pdf":
        text = read_pdf(path)
        if not text.strip():
            return [], "pdf-geen-extractie"
        return [("", text)], "pdf"
    if ext == ".eml":
        with open(path, "rb") as f:
            return [("", read_eml(f.read()))], "eml"

    with open(path, encoding="utf-8", errors="replace") as f:
        raw = f.read()

    if ext in (".vtt", ".srt"):
        return read_subtitles(raw), "ondertitel-transcript"
    if ext in (".html", ".htm"):
        return [("", strip_html(raw))], "html"

    stripped = raw.lstrip()
    if stripped.startswith("["):
        try:
            items = read_json_transcript(raw)
            if items:
                return items, "json-transcript"
        except json.JSONDecodeError:
            pass
    if stripped.startswith("{"):
        items = read_jsonl_transcript(raw)
        if items:
            return items, "jsonl-transcript"
    return [("", raw)], "tekst"


def clean_transcript(items):
    kept, dropped = [], 0
    prev_norm = ""
    for spk, s in items:
        n = _norm(s)
        if not n or n in FILLERS or len(n.split()) <= 1:
            dropped += 1
            continue
        if n == prev_norm:
            dropped += 1
            continue
        prev_norm = n
        if kept and kept[-1][0] == spk:
            kept[-1] = (spk, kept[-1][1] + " " + s)
        else:
            kept.append((spk, s))
    return kept, dropped


def chunk_text(turns) -> list:
    chunks, cur, cur_tokens = [], [], 0
    for spk, text in turns:
        for para in re.split(r"\n{2,}", text) if not spk else [text]:
            para = para.strip()
            if not para:
                continue
            line = f"{spk}: {para}" if spk else para
            t = memlib.token_estimate(line)
            if cur and cur_tokens + t > CHUNK_TOKENS:
                chunks.append("\n".join(cur))
                cur, cur_tokens = [], 0
            cur.append(line)
            cur_tokens += t
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def main() -> int:
    if len(sys.argv) < 2:
        print("gebruik: import_prep.py <bronbestand> [--root <pad>]")
        return 1
    src_path = sys.argv[1]
    root = memlib.find_project_root(
        sys.argv[sys.argv.index("--root") + 1] if "--root" in sys.argv else os.getcwd())
    if not os.path.isfile(src_path):
        print(f"bestand niet gevonden: {src_path}")
        return 1

    try:
        items, fmt = parse_source(src_path)
    except (zipfile.BadZipFile, KeyError):
        print("kon dit .docx-bestand niet lezen (beschadigd of oud .doc-formaat); "
              "sla het opnieuw op als .docx of .pdf")
        return 1

    if fmt == "pdf-geen-extractie":
        print("pdf: geen tekstextractie beschikbaar op deze machine "
              "(pdftotext en pypdf ontbreken, of het is een gescande PDF).")
        print(f"FALLBACK: lees het PDF direct met de Read-tool: {os.path.abspath(src_path)}")
        return 0

    if "transcript" in fmt:
        turns, dropped = clean_transcript(items)
    else:
        turns, dropped = items, 0

    chunks = chunk_text(turns)
    if not chunks:
        print("geen leesbare tekst gevonden in dit bestand")
        return 1

    out_dir = os.path.join(memlib.ensure_store(memlib.project_store(root)), "imports")
    os.makedirs(out_dir, exist_ok=True)
    gi = os.path.join(memlib.project_store(root), ".gitignore")
    have = set()
    if os.path.isfile(gi):
        with open(gi, encoding="utf-8") as f:
            have = {l.strip() for l in f}
    if "imports/" not in have:
        with open(gi, "a", encoding="utf-8") as f:
            f.write("imports/\n")

    base = re.sub(r"[^\w\-]", "-", os.path.splitext(os.path.basename(src_path))[0])[:40]
    paths = []
    for i, chunk in enumerate(chunks, start=1):
        suffix = f"-{i:02d}" if len(chunks) > 1 else ""
        p = os.path.join(out_dir, f"{base}{suffix}.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(chunk)
        paths.append(p)

    total = sum(memlib.token_estimate(c) for c in chunks)
    orig = os.path.getsize(src_path) // 4
    print(f"formaat: {fmt}")
    print(f"origineel: ~{orig} tokens | schoon: ~{total} tokens"
          + (f" ({dropped} vulzinnen/herhalingen verwijderd)" if dropped else ""))
    print(f"{len(paths)} chunk(s):")
    for p in paths:
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
