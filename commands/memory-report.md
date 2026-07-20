---
description: Genereer het HTML-dashboard - activiteit, tijdlijn, beslisgeschiedenis, kosten en gebruik
allowed-tools: Bash(python3:*), Bash(open:*)
---

Genereer het memory-rapport. Voer uit:

```
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/report.py"
```

Noem het pad van het gegenereerde bestand en vat het advies samen. Bied aan het rapport te openen met `open <pad>` (macOS). Het dashboard toont kerncijfers, activiteit per week (nieuwe entries versus reads door Claude), een tijdlijn van wat er is gedaan (nieuw, vervangen, verouderd, triage, onderhoud), de beslisgeschiedenis, de topics-tabel met tokenkosten en leesfrequentie, en snoei-advies.
