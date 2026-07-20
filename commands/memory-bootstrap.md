---
description: Bouw een start-memory uit de git-historie van een bestaand project (commits, TODO's, README)
argument-hint: [max aantal commits, default 120]
allowed-tools: Bash(python3:*), Read
---

Bouw een start-memory voor dit bestaande project uit zijn eigen git-historie.

## Stap 1: mijnen

```
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/bootstrap.py"
```

Dit verzamelt relevante commits (met hun body, waar de rationale staat), TODO/FIXME-comments, de README-kop en tags in chunks onder .claude/memory/imports/.

## Stap 2: lezen en destilleren

Lees elke chunk met Read en destilleer, streng en met bronvermelding:

- **decisions**: commits die een keuze of omslag beschrijven ("switch to X", "vervang Y door Z", "van A naar B"). De commit-body bevat vaak de waarom; neem die op. Bron: "(afgeleid uit commit <hash>)".
- **gotchas**: TODO/FIXME/HACK-comments die een echt risico of bekende beperking beschrijven. Neem het bestandspad op als --refs zodat de verificatie erop werkt. Sla triviale TODO's over.
- **context**: wat de README over doel en opzet van het project zegt, plus wat de release-tags over de fasering vertellen.
- **conventions**: patronen die uit meerdere commits samen blijken (bijv. consequent dezelfde structuur of tooling).

Regels: maximaal 15 entries; commits beschrijven wat er GEBEURD is, dus formuleer als vastgesteld feit, niet als plan; onzekere interpretaties markeren met "(interpretatie)"; verouderde besluiten die later aantoonbaar zijn teruggedraaid (zichtbaar in latere commits) niet opnemen of als vervangen context noemen.

## Stap 3: opslaan

Per entry, met refs waar van toepassing:

```
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/memlib.py" add --topic <topic> --title "<titel>" --keywords "<kw1,kw2,kw3>" --body "<entry> (afgeleid uit commit <hash>)" [--refs "pad/naar/bestand.ext"]
```

## Stap 4: samenvatten

Meld hoeveel entries per topic, welke interpretaties onzeker zijn, en adviseer de gebruiker de entries even door te lezen: een bootstrap is een reconstructie, geen kickoff. Vanaf nu vult de normale triage het geheugen verder aan.
