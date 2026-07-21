---
description: Importeer een document of gesprekstranscript, destilleer naar memory-entries en stel features voor
argument-hint: <pad naar bestand> [doeltopic]
allowed-tools: Bash(python3:*), Read
---

Importeer het volgende bronbestand naar de projectmemory: $ARGUMENTS

Ondersteunde formaten: gesprekstranscripten (.json/.jsonl), Teams/Zoom-ondertitels (.vtt/.srt), Word (.docx), PDF, e-mail (.eml), HTML en platte tekst/Markdown. Meldt de voorbewerking een PDF-fallback, lees het PDF dan direct met Read en ga verder bij stap 2.

## Stap 1: voorbewerken

```
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/import_prep.py" "<pad>"
```

Dit maakt het bestand schoon (smalltalk en vulzinnen eruit bij transcripten) en levert een of meer chunk-bestanden op.

## Stap 2: lezen en destilleren

Lees elke chunk met Read. Extraheer alleen inhoud die blijvende waarde heeft voor het project:

- **requirements**: gewenste features, functionele eisen, dingen die de klant wil
- **decisions**: genomen besluiten en keuzes
- **gotchas**: risico's, beperkingen, valkuilen die genoemd worden
- **klanten** of **context**: relevante feiten over de klant, het domein, de werkwijze

Regels bij het destilleren:
1. Negeer smalltalk, herhalingen en alles zonder projectwaarde. Bij een kickoff-transcript is doorgaans 80 tot 95 procent ruis; wees streng.
2. Transcriptiefouten mag je herstellen als de bedoeling evident is; markeer onzekere interpretaties in de entry met "(interpretatie)".
3. Formuleer elke entry als zelfstandige, begrijpelijke zin of korte alinea. Niet citeren, wel destilleren.
4. Maximaal 15 entries per import; liever 8 goede dan 15 matige.

## Stap 3: opslaan

Per entry:

```
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/memlib.py" add --topic <topic> --title "<titel, max 9 woorden>" --keywords "<3-6 trefwoorden>" --body "<entry> [bron: <bestandsnaam>, geimporteerd]"
```

Secret-scrubbing, deduplicatie en conflictdetectie gelden automatisch. Is er een doeltopic meegegeven in de opdracht, gebruik dan dat topic voor alle entries.

Zegt de gebruiker "globaal" of "klant", voeg dan --store global respectievelijk --store customer toe aan elk add-commando.

## Stap 4: samenvatting

Geef de gebruiker een compacte samenvatting van wat er is opgeslagen: per topic de entries in 1 regel, plus de onzekere interpretaties. Dit is het controlemoment; de bron-chunks staan in .claude/memory/imports/ (gitignored).

## Stap 5: featurevoorstel

Bouw daarna een geprioriteerd featurevoorstel. BELANGRIJK voor tokenefficiëntie: baseer dit uitsluitend op de zojuist opgeslagen entries uit stap 3/4, niet door de chunks opnieuw te lezen. De entries zijn de samenvatting; de chunks zijn vanaf hier niet meer nodig.

Formaat per feature: naam, 1 zin wat het doet, welke requirement-entries het dekt, prioriteit (must/should/could) met korte motivering vanuit de klantcontext (bijv. piekbelasting, geen omvangsgroei).

Sluit af met: "Zeg 'onthoud: MVP-scope is ...' om de gekozen scope als besluit vast te leggen." Sla het voorstel NIET automatisch op; een voorstel wordt pas een besluit als de gebruiker kiest.

Is de Atlassian MCP-server verbonden, bied dan ook aan: "Ik kan de gekozen features direct als Jira-issues aanmaken; zeg welk project." Zet bij het aanmaken de dekkende requirement-entries in de issue-beschrijving en meld de aangemaakte issue-keys, zodat de gebruiker ze aan memory-entries kan koppelen.
