---
description: Bekijk of wijzig de memory-instellingen van dit project (scope, injectie, budgetten)
argument-hint: [scope project|global|both|off] [injection hint|full|off]
allowed-tools: Bash(python3:*)
---

Beheer de memory-configuratie van dit project. Verzoek van de gebruiker: $ARGUMENTS

Huidige instellingen tonen:

```
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/memlib.py" config --show
```

Wijzigen (alleen de flags meesturen die de gebruiker wil aanpassen):

```
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/memlib.py" config --scope both --injection hint
```

Betekenis van scope: project (alleen deze repo), global (alleen ~/.claude, voor persoonlijke voorkeuren over projecten heen), both (beide, index toont herkomst), off (plugin doet niets in dit project). Injectie: hint (1 regel met verwijzing per matchend topic, Claude leest zelf bij), full (hele entries binnen budget), off (alleen de index bij sessiestart).

Toon na een wijziging de nieuwe configuratie en vat in 1 zin samen wat er verandert.
