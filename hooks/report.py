#!/usr/bin/env python3
"""Dashboard over de memory: wat is er gedaan, wat kost het, wat wordt gebruikt.

Twee vormen, beide zonder server of dependencies:
  python3 report.py                  -> HTML-dashboard in .claude/memory/report.html
  python3 report.py --recent [N]     -> terminal-tijdlijn van de laatste N dagen (default 14)

Databronnen: entry-datums, het archief (vervangen-door / verouderd-notities),
de .log van elke store (triage, injecties, imports) en .usage.jsonl (reads).
"""

import argparse
import datetime
import html
import os
import re
import signal
import sys

try:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except (AttributeError, ValueError):
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memlib  # noqa: E402


# ------------------------------------------------------------- verzamelen ---

def collect_events(cfg, root):
    """[(datum-iso, type, omschrijving, storelabel)] nieuwste eerst."""
    events = []
    for label, store in memlib.read_stores(cfg, root):
        if not os.path.isdir(store):
            continue
        for e in memlib.all_entries(store):
            events.append((e["date"], "nieuw", f"[{e['topic']}] {e['title']}", label))
            if e.get("supersedes"):
                events.append((e["date"], "vervangt", f"[{e['topic']}] '{e['supersedes']}' -> '{e['title']}'", label))
        arch = os.path.join(store, "archive")
        if os.path.isdir(arch):
            for f in os.listdir(arch):
                if not f.endswith(".md"):
                    continue
                for e in memlib.parse_entries(os.path.join(arch, f), f[:-3]):
                    m = re.search(r"^vervangen-door: (\d{4}-\d{2}-\d{2})", e["body"], re.M)
                    if m:
                        events.append((m.group(1), "vervangen", f"[{e['topic']}] {e['title']}", label))
                    elif re.search(r"^verouderd:", e["body"], re.M):
                        events.append((e["date"], "verouderd", f"[{e['topic']}] {e['title']}", label))
        logpath = os.path.join(store, ".log")
        if os.path.isfile(logpath):
            with open(logpath, encoding="utf-8") as f:
                for line in f:
                    stamp, _, msg = line.strip().partition(" ")
                    day = stamp[:10]
                    if not re.match(r"\d{4}-\d{2}-\d{2}", day):
                        continue
                    if "stop_triage:" in msg and "nieuwe entries" in msg:
                        events.append((day, "triage", msg.split("stop_triage: ")[1], label))
                    elif "direct opgeslagen" in msg:
                        events.append((day, "onthoud", msg.split("prompt_submit: ")[1], label))
                    elif "consolidate:" in msg:
                        events.append((day, "onderhoud", msg.split("consolidate: ")[1], label))
                    elif "validator:" in msg:
                        events.append((day, "validator", msg.split("validator: ")[1], label))
    events.sort(key=lambda ev: ev[0], reverse=True)
    deduped, prev = [], None
    for ev in events:
        if ev != prev:
            deduped.append(ev)
        prev = ev
    return deduped


def weekly_activity(cfg, root, weeks=8):
    """Per week: (label, nieuw, reads)."""
    today = datetime.date.today()
    buckets = []
    for w in range(weeks - 1, -1, -1):
        start = today - datetime.timedelta(days=today.weekday() + 7 * w)
        buckets.append((start, start + datetime.timedelta(days=6), 0, 0))
    new_per_day, reads_per_day = {}, {}
    for label, store in memlib.read_stores(cfg, root):
        if not os.path.isdir(store):
            continue
        for e in memlib.all_entries(store):
            new_per_day[e["date"]] = new_per_day.get(e["date"], 0) + 1
        upath = os.path.join(store, ".usage.jsonl")
        if os.path.isfile(upath):
            import json as _json
            with open(upath, encoding="utf-8") as f:
                for line in f:
                    try:
                        d = _json.loads(line).get("ts", "")[:10]
                        reads_per_day[d] = reads_per_day.get(d, 0) + 1
                    except Exception:
                        continue
    out = []
    for start, end, _n, _r in buckets:
        n = r = 0
        d = start
        while d <= end:
            iso = d.isoformat()
            n += new_per_day.get(iso, 0)
            r += reads_per_day.get(iso, 0)
            d += datetime.timedelta(days=1)
        out.append((start.strftime("%d %b"), n, r))
    return out


def injection_stats(store: str, days: int = 30):
    path = os.path.join(store, ".log")
    if not os.path.isfile(path):
        return 0, 0
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
    count = tokens = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line[:19] < cutoff:
                continue
            m = re.search(r"\((\d+) tokens", line)
            if m and ("injectie" in line or "index" in line):
                count += 1
                tokens += int(m.group(1))
    return count, tokens


def savings(cfg, root, days=30):
    """Bespaarde tokens t.o.v. de nulmeting 'alle actieve memory altijd in
    context' (de CLAUDE.md-aanpak). Werkelijk verbruik = geinjecteerde tokens
    (uit .log) + tokens van topicbestanden die Claude echt las (uit .usage)."""
    import json as _json
    cutoff_dt = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
    total_active = sessions = injected = read_tokens = 0
    for _label, store in memlib.read_stores(cfg, root):
        if not os.path.isdir(store):
            continue
        topic_tokens = {}
        for t in memlib.list_topics(store):
            entries = memlib.topic_entries(store, t)
            tok = memlib.token_estimate("".join(e["title"] + e["body"] for e in entries))
            topic_tokens[t] = tok
            total_active += tok
        logpath = os.path.join(store, ".log")
        if os.path.isfile(logpath):
            with open(logpath, encoding="utf-8") as f:
                for line in f:
                    if line[:19] < cutoff_dt:
                        continue
                    if "session_start:" in line:
                        sessions += 1
                    m = re.search(r"\((\d+) tokens", line)
                    if m and ("injectie" in line or "index" in line):
                        injected += int(m.group(1))
        upath = os.path.join(store, ".usage.jsonl")
        if os.path.isfile(upath):
            with open(upath, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = _json.loads(line)
                    except Exception:
                        continue
                    if rec.get("ts", "") >= cutoff_dt:
                        read_tokens += topic_tokens.get(rec.get("topic", ""), 0)
    baseline = sessions * total_active
    actual = injected + read_tokens
    saved = max(0, baseline - actual)
    euro = saved / 1_000_000 * cfg["price_eur_per_mtok"]
    return {"days": days, "sessions": sessions, "total_active": total_active,
            "baseline": baseline, "actual": actual, "saved": saved, "euro": euro}


def topic_rows(cfg, root):
    rows = []
    for label, store in memlib.read_stores(cfg, root):
        if not os.path.isdir(store):
            continue
        usage30 = memlib.usage_counts(store, days=30)
        usage90 = memlib.usage_counts(store, days=90)
        for t in memlib.list_topics(store):
            entries = memlib.topic_entries(store, t)
            if not entries:
                continue
            rows.append({
                "store": label, "topic": t, "entries": len(entries),
                "tokens": memlib.token_estimate("".join(e["title"] + e["body"] for e in entries)),
                "reads30": usage30.get(t, 0), "reads90": usage90.get(t, 0),
                "latest": max(e["date"] for e in entries),
            })
    rows.sort(key=lambda x: (-x["reads30"], -x["reads90"], x["topic"]))
    return rows


def advice(rows):
    tips = []
    grace = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    has_usage = any(r["reads90"] for r in rows)
    if not has_usage and all(r["latest"] >= grace for r in rows):
        tips.append("Nog geen leesdata (memory is jonger dan 30 dagen); gebruiksadvies volgt vanzelf.")
    for r in rows:
        if has_usage and r["reads90"] == 0 and r["latest"] < grace:
            tips.append(f"[{r['store']}] {r['topic']} is 90 dagen niet gelezen "
                        f"({r['entries']} entries). Kandidaat voor archief.")
        elif r["tokens"] > 2000:
            tips.append(f"[{r['store']}] {r['topic']} is groot (~{r['tokens']} tokens); "
                        "overweeg opsplitsen.")
    if not tips:
        tips.append("Geen opvallende zaken.")
    return tips


BADGES = {"nieuw": "#2d7d46", "vervangt": "#8250df", "vervangen": "#8250df",
          "verouderd": "#b35900", "triage": "#0969da", "onthoud": "#0969da",
          "onderhoud": "#6e7781", "validator": "#cf222e"}


# ------------------------------------------------------------------- html ---

def bar_chart_svg(weeks):
    if not weeks:
        return ""
    w, h, pad = 660, 150, 30
    maxv = max(max(n, r) for _l, n, r in weeks) or 1
    bw = (w - pad) / len(weeks) / 2.6
    parts = [f'<svg viewBox="0 0 {w} {h + 30}" width="100%" role="img">']
    for i, (label, n, r) in enumerate(weeks):
        x = pad + i * (w - pad) / len(weeks)
        hn, hr = h * n / maxv, h * r / maxv
        parts.append(f'<rect x="{x:.0f}" y="{h - hn:.0f}" width="{bw:.0f}" height="{hn:.0f}" fill="#2d7d46"><title>{label}: {n} nieuw</title></rect>')
        parts.append(f'<rect x="{x + bw + 2:.0f}" y="{h - hr:.0f}" width="{bw:.0f}" height="{hr:.0f}" fill="#0969da"><title>{label}: {r} reads</title></rect>')
        parts.append(f'<text x="{x:.0f}" y="{h + 16}" font-size="10" fill="#57606a">{label}</text>')
    parts.append(f'<text x="{pad}" y="12" font-size="11"><tspan fill="#2d7d46">■</tspan> nieuwe entries  <tspan fill="#0969da">■</tspan> reads door Claude</text>')
    parts.append("</svg>")
    return "".join(parts)


def render_html(cfg, root):
    e = html.escape
    rows = topic_rows(cfg, root)
    events = collect_events(cfg, root)
    weeks = weekly_activity(cfg, root)
    total = sum(r["entries"] for r in rows)
    reads30 = sum(r["reads30"] for r in rows)
    inj = [injection_stats(s) for _l, s in memlib.read_stores(cfg, root) if os.path.isdir(s)]
    inj_n, inj_tok = sum(i[0] for i in inj), sum(i[1] for i in inj)
    sav = savings(cfg, root)

    timeline = ""
    for date, kind, desc, store in events[:25]:
        color = BADGES.get(kind, "#6e7781")
        timeline += (f'<tr><td class="d">{date}</td>'
                     f'<td><span class="b" style="background:{color}">{kind}</span></td>'
                     f'<td>{e(desc[:110])}</td><td class="d">{e(store)}</td></tr>')

    table = ""
    for r in rows:
        cls = ' class="warn"' if r["reads90"] == 0 else ""
        table += (f'<tr{cls}><td>{e(r["store"])}</td><td>{e(r["topic"])}</td>'
                  f'<td>{r["entries"]}</td><td>{r["tokens"]}</td>'
                  f'<td>{r["reads30"]}</td><td>{r["latest"]}</td></tr>')

    chains = [ev for ev in events if ev[1] in ("vervangt",)][:8]
    chain_html = "".join(f"<li>{ev[0]}: {e(ev[2])}</li>" for ev in chains) or "<li>nog geen vervangen besluiten</li>"
    tips = "".join(f"<li>{e(t)}</li>" for t in advice(rows))
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!DOCTYPE html><html lang="nl"><head><meta charset="utf-8">
<title>Memory-dashboard</title><style>
body{{font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#1f2328}}
h1{{border-bottom:2px solid #1f2328;padding-bottom:.3rem}}
.cards{{display:flex;gap:1rem;flex-wrap:wrap;margin:1rem 0}}
.card{{flex:1;min-width:130px;border:1px solid #d0d7de;border-radius:8px;padding:.8rem;text-align:center}}
.card .n{{font-size:1.6rem;font-weight:700}}
.card .l{{font-size:.75rem;color:#57606a}}
table{{border-collapse:collapse;width:100%;margin:.5rem 0 1.5rem}}
th,td{{border:1px solid #d0d7de;padding:.35rem .6rem;text-align:left;font-size:.85rem}}
th{{background:#f6f8fa}} tr.warn td{{background:#fff8e5}}
.b{{color:#fff;border-radius:10px;padding:.1rem .5rem;font-size:.7rem}}
.d{{color:#57606a;white-space:nowrap}}
.meta{{color:#57606a;font-size:.85rem}}
</style></head><body>
<h1>Memory-dashboard</h1>
<p class="meta">{stamp} | scope {e(cfg['scope'])} | retrieval {e(cfg['retrieval'])} | budget index {cfg['index_budget']} / retrieval {cfg['retrieval_budget']} tokens</p>
<div class="cards">
<div class="card"><div class="n">{total}</div><div class="l">actieve entries</div></div>
<div class="card"><div class="n">{reads30}</div><div class="l">reads door Claude (30d)</div></div>
<div class="card"><div class="n">{inj_n}</div><div class="l">injecties (30d)</div></div>
<div class="card"><div class="n">{inj_tok}</div><div class="l">injectie-tokens (30d)</div></div>
<div class="card"><div class="n">{sav['saved']:,}</div><div class="l">tokens bespaard (30d)</div></div>
<div class="card"><div class="n">&euro;{sav['euro']:.2f}</div><div class="l">indicatieve waarde (30d)</div></div>
</div>
<p class="meta">Besparing = nulmeting ({sav['sessions']} sessies x {sav['total_active']:,} tokens actieve memory, de "alles altijd in CLAUDE.md"-aanpak) minus werkelijk verbruik ({sav['actual']:,} tokens aan injecties en reads). Euro-waarde op basis van &euro;{cfg['price_eur_per_mtok']}/miljoen input-tokens (instelbaar via price_eur_per_mtok); indicatief, want abonnementen betalen per plan en prompt caching verlaagt de werkelijke nulmeting.</p>
<h2>Activiteit per week</h2>{bar_chart_svg(weeks)}
<h2>Wat is er gedaan</h2>
<table><tr><th>datum</th><th>type</th><th>gebeurtenis</th><th>store</th></tr>{timeline}</table>
<h2>Beslisgeschiedenis</h2><ul>{chain_html}</ul>
<h2>Topics</h2>
<table><tr><th>store</th><th>topic</th><th>entries</th><th>~tokens</th><th>reads 30d</th><th>laatste</th></tr>{table}</table>
<h2>Advies</h2><ul>{tips}</ul>
</body></html>"""


# --------------------------------------------------------------- terminal ---

def print_recent(cfg, root, days):
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    events = [ev for ev in collect_events(cfg, root) if ev[0] >= cutoff]
    if not events:
        print(f"Geen memory-activiteit in de afgelopen {days} dagen.")
        return
    print(f"Memory-activiteit, laatste {days} dagen ({len(events)} gebeurtenissen):\n")
    cur = None
    for date, kind, desc, store in events:
        if date != cur:
            print(f"{date}")
            cur = date
        print(f"  {kind:10s} [{store}] {desc[:95]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=os.getcwd())
    parser.add_argument("--recent", nargs="?", const=14, type=int, default=None)
    args = parser.parse_args()
    root = memlib.find_project_root(args.root)
    cfg = memlib.load_config(root)
    if not memlib.read_stores(cfg, root):
        print("Memory staat uit voor dit project.")
        return 0
    if args.recent is not None:
        print_recent(cfg, root, args.recent)
        return 0
    out = os.path.join(memlib.project_store(root), "report.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(render_html(cfg, root))
    print(f"Dashboard geschreven: {out}")
    rows = topic_rows(cfg, root)
    sav = savings(cfg, root)
    print(f"  bespaard (30d): ~{sav['saved']:,} tokens = ~EUR {sav['euro']:.2f} "
          f"({sav['sessions']} sessies, nulmeting {sav['baseline']:,} tokens, "
          f"werkelijk {sav['actual']:,})")
    for t in advice(rows):
        print(f"  advies: {t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
