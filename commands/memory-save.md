---
description: Sla handmatig een memory-entry op
argument-hint: <wat je wilt onthouden> [globaal]
allowed-tools: Bash(python3:*)
---

Sla het volgende op in de memory: $ARGUMENTS

1. Bepaal het topic: decisions (techniekkeuze), conventions (stijl of afspraak), gotchas (valkuil of bekende bug), context (overig) of een eigen topicnaam als het onderwerp daar duidelijk om vraagt.
2. Zegt de gebruiker "globaal" of gaat het om een persoonlijke voorkeur die niet aan dit project hangt, voeg dan --store global toe.
3. Formuleer een titel van maximaal 9 woorden en 3 tot 6 trefwoorden, en voer uit:

```
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/memlib.py" add --topic <topic> --title "<titel>" --keywords "<kw1,kw2,kw3>" --body "<volledige tekst>" [--store global]
```

Bevestig kort wat er is opgeslagen, in welk topic en in welke store.
