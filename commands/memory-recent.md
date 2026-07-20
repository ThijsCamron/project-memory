---
description: Terminal-overzicht van recente memory-activiteit (wat is er gedaan)
argument-hint: [aantal dagen, default 14]
allowed-tools: Bash(python3:*)
---

Toon de recente memory-activiteit. Voer uit:

```
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/report.py" --recent <dagen>
```

Vat de tijdlijn kort samen voor de gebruiker: hoeveel nieuwe entries, of er besluiten zijn vervangen of verouderd geraakt, en of de validator iets meldde. Wil de gebruiker het visuele overzicht met grafiek en beslisgeschiedenis, wijs dan op /project-memory:memory-report.
