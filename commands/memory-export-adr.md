---
description: Exporteer alle beslissingen als Architecture Decision Records naar docs/adr/
allowed-tools: Bash(python3:*)
---

Exporteer de decisions uit de projectmemory (actieve en vervangen) als genummerde ADR-bestanden. Voer uit:

```
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/memlib.py" export-adr
```

Vat samen hoeveel ADR's zijn geschreven en noem de paden. Vervangen beslissingen krijgen status "Vervangen" met een verwijzing naar hun opvolger; zo blijft de beslisgeschiedenis leesbaar voor het hele team.
