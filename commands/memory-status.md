---
description: Toon wat er is opgeslagen en wat het aan tokens kost
allowed-tools: Bash(python3:*)
---

Toon de status van de projectmemory. Voer uit:

```
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/consolidate.py" --status
```

Geef de uitkomst weer aan de gebruiker. Ligt de index boven het budget, adviseer dan consolidatie:

```
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/consolidate.py"
```
