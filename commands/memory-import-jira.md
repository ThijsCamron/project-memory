---
description: Destilleer blijvende kennis uit een bestaand Jira-project naar memory-entries (vereist Atlassian MCP)
argument-hint: <Jira projectkey, bijv. DAKDEKKER>
allowed-tools: Bash(python3:*)
---

Destilleer blijvende kennis uit Jira-project $ARGUMENTS naar de projectmemory.

Vereist: de Atlassian MCP-server is verbonden. Zo niet, meld dat en geef het setup-commando:
`claude mcp add --transport http --scope project atlassian https://mcp.atlassian.com/v1/mcp`

## Wat WEL importeren en wat NIET

Jira blijft de bron van waarheid voor issues en hun status. Importeer daarom GEEN issue-titels, statussen of de backlog; die verouderen per dag en horen niet in memory. Importeer alleen kennis die stabiel blijft:

- **decisions**: besluiten die in issues of comments zijn vastgelegd ("we kiezen voor X omdat...")
- **gotchas**: beperkingen, workarounds en valkuilen die in comments zijn ontdekt
- **requirements**: functionele eisen die het niveau van een enkel issue overstijgen
- **klanten/context**: domeinkennis over de klant die in beschrijvingen staat

## Stappen

1. Haal via de Atlassian MCP-tools de issues van project $ARGUMENTS op. Beperk je tot afgeronde en actieve issues met inhoudelijke beschrijvingen of comments; sla triviale taken over.
2. Destilleer maximaal 15 entries volgens de regels hierboven. Formuleer elke entry zelfstandig begrijpelijk.
3. Zet bij elke entry de relevante issue-key(s) in de trefwoorden (bijv. "dakdekker-123"), zodat "werk aan DAKDEKKER-123" later automatisch de context erbij haalt.
4. Sla op per entry:

```
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/memlib.py" add --topic <topic> --title "<titel>" --keywords "<kw1,kw2,issue-key>" --body "<entry> [bron: Jira $ARGUMENTS]"
```

5. Vat samen: hoeveel entries per topic, en welke issue-keys nu gekoppeld zijn. Herinner de gebruiker eraan dat issue-status altijd live uit Jira komt, nooit uit memory.
