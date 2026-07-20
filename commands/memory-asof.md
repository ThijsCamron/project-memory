---
description: Tijdmachine - toon wat het team op een moment in de git-historie wist
argument-hint: <commit, tag of datum zoals 2026-03> [vraag]
allowed-tools: Bash(python3:*), Read
---

Reconstrueer de memory-staat van dit project op het opgegeven moment: $ARGUMENTS

1. Voer uit: `python3 "${CLAUDE_PLUGIN_ROOT}/hooks/asof.py" "<ref-of-datum>"`
2. De uitvoer toont wat het team toen wist, wat er sindsdien is bijgeleerd en wat er is vervangen. De volledige snapshot staat in .claude/memory/asof/ en kun je met Read raadplegen.
3. Bevat de opdracht een vraag ("waarom is X toen zo gebouwd?"), beantwoord die dan strikt vanuit de kennis van TOEN, en benoem expliciet welke relevante kennis er op dat moment nog NIET was. Dat laatste is meestal het echte antwoord.
