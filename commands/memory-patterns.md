---
description: Vind kennis die in meerdere projecten terugkomt (kandidaten voor de baseline)
argument-hint: <map met projectrepo's, bijv. ~/werk>
allowed-tools: Bash(python3:*)
---

Scan de opgegeven map op terugkerende kennis over projecten heen: $ARGUMENTS

1. Voer uit: `python3 "${CLAUDE_PLUGIN_ROOT}/hooks/patterns.py" "<map>"`
2. Bespreek per gevonden patroon of promotie zinvol is: naar de klant-store (zelfde klant, meerdere projecten) of de globale baseline (bedrijfsbreed).
3. Wil de gebruiker promoveren, formuleer dan een samengevoegde entry uit de clusterleden en sla op met `--store customer` of `--store global`. Verwijder de projectvarianten NIET; de conflictdetectie en consolidatie ruimen vanzelf op.
