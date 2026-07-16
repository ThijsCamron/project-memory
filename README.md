# project-memory


Per-project memory voor Claude Code, gebouwd op het pull-model: bij sessiestart komt alleen een compacte index in de context (1 regel per onderwerp, met pad en samenvatting). Wil Claude details, dan leest het zelf gericht één topicbestand met Read of Grep. De opslag mag daardoor zo groot worden als je wilt; je betaalt alleen voor wat daadwerkelijk wordt opgevraagd. Alle triage en consolidatie draait in Python-scripts buiten het model en kost zelf geen tokens.

## Scope: per project instelbaar

| Scope | Gedrag |
|---|---|
| `project` (default) | alleen `.claude/memory/` in de repo |
| `global` | alleen `~/.claude/project-memory/global/`, voor persoonlijke voorkeuren over projecten heen |
| `both` | beide; de index toont per regel de herkomst `[project]` of `[globaal]` |
| `off` | plugin doet niets in dit project |

Instellen per project via `/project-memory:memory-config scope both` of direct:

```
python3 hooks/memlib.py config --scope both --injection hint
```

De instelling landt in `.claude/memory/config.json` en reist mee met de repo. Volgorde: defaults < globale config < projectconfig < omgevingsvariabelen.

## Injectiemodus

| Modus | Gedrag |
|---|---|
| `hint` (default) | per matchend topic 1 regel met het pad; Claude leest zelf bij als het nodig is |
| `full` | hele entries in de context, binnen het retrieval-budget |
| `off` | alleen de index bij sessiestart |

## Wat er automatisch gebeurt

1. **SessionStart** injecteert de merged index binnen `index_budget` (default 300 tokens), met de instructie om details zelf op te halen.
2. **UserPromptSubmit** matcht je prompt tegen alle topics en injecteert volgens de gekozen modus. Slash commands en niet-matchende prompts krijgen niets.
3. **Stop** scant het transcript op signaalzinnen ("besluit:", "voortaan", "conventie:", "let op", "onthoud", "from now on" en varianten), dedupliceert en schrijft maximaal 8 nieuwe entries per sessie naar de write-store die uit de scope volgt.
4. **Consolidatie** draait hooguit 1x per 24 uur per store: duplicaten eruit, entries ouder dan `archive_days` naar `archive/`, cap van `max_entries` per topic.

## Opslag

```
.claude/memory/               (projectstore; globale store heeft dezelfde vorm)
├── config.json               scope, injectie, budgetten
├── index.md                  auto-gegenereerd
├── topics/
│   ├── decisions.md          techniek- en architectuurkeuzes
│   ├── conventions.md        stijl en afspraken
│   ├── gotchas.md            valkuilen en bekende bugs
│   └── <eigen-topic>.md      vrij uit te breiden via /memory-save
└── archive/
```

## Commands

| Command | Doet |
|---|---|
| `/project-memory:memory-config` | toon of wijzig scope, injectie en budgetten |
| `/project-memory:memory-save <tekst> [globaal]` | sla handmatig een entry op |
| `/project-memory:memory-search <termen>` | doorzoek alle actieve stores |
| `/project-memory:memory-status` | aantallen en tokenkosten per store |
| `/project-memory:memory-export-adr` | beslissingen als ADR's naar docs/adr/ |

## Installatie en configuratie

```
claude --plugin-dir /pad/naar/project-memory
```

Omgevingsvariabelen (winnen van elke config): `MEMORY_SCOPE`, `MEMORY_INJECTION`, `MEMORY_INDEX_BUDGET`, `MEMORY_RETRIEVAL_BUDGET`, `MEMORY_ARCHIVE_DAYS`, `MEMORY_MAX_ENTRIES`.

## Semantische retrieval (v0.4)

De Markdown blijft de bron van waarheid; daarnaast onderhoudt de plugin per store een herbouwbare SQLite-index (`.index.db`, staat in .gitignore) met embedding-vectoren. Zoeken gebeurt met brute-force cosine similarity: op memory-schaal is dat milliseconden werk, dus een aparte vector-database is bewust weggelaten. De index synct incrementeel na elke triage en consolidatie; weggooien is veilig, hij wordt opnieuw opgebouwd.

Retrieval-modus (`retrieval`): `keyword`, `semantic` of `hybrid` (default; een topic matcht als een van beide routes hem vindt). Embedding-backend (`embedding_backend`):

| Backend | Vereist | Karakter |
|---|---|---|
| `hash` (default) | niets | feature-hashing, lexicaal, deterministisch, offline |
| `local` | `pip install sentence-transformers` | echte semantiek, volledig lokaal (all-MiniLM-L6-v2) |
| `voyage` | env `VOYAGE_API_KEY` | Voyage AI API (voyage-3.5-lite) |
| `openai` | env `OPENAI_API_KEY` | OpenAI API (text-embedding-3-small) |

Instellen: `/project-memory:memory-config` of `memlib.py config --embedding-backend local --retrieval hybrid`. Is een backend onbeschikbaar (library of key ontbreekt, netwerkfout), dan valt alles automatisch terug op `hash` met een logregel; de hooks blokkeren nooit op een externe dienst. Let op bij `voyage`/`openai`: de tekst van entries en prompts gaat dan naar die API. Embedden kost geen Claude-tokens; de winst zit in precisie, waardoor minder verkeerde topics worden geinjecteerd of gelezen.

## Geverifieerde teamkennis (v0.3)

**Secret-scrubbing.** Elke entry wordt voor opslag gescand op AWS-keys, sk-tokens, GitHub- en Slack-tokens, JWT's, bearer tokens en `password=`/`api_key=`-patronen; treffers worden vervangen door `[GEREDIGEERD]`. Entries met een private key worden geweigerd. Dit staat altijd aan, juist omdat de memory in git belandt.

**Git-geverifieerde refs.** Entries kunnen verwijzen naar bestanden (`refs: src/auth/jwt.py`); de triage pikt paden uit zinnen automatisch op als het bestand echt bestaat. De dagelijkse consolidatie controleert alle refs en archiveert entries waarvan de code is verdwenen, met de reden erbij. Verouderde memory ruimt zichzelf op.

**Conflictdetectie met beslisgeschiedenis.** Een nieuwe entry in decisions of conventions die 3 of meer trefwoorden deelt met een bestaande, vervangt die: de oude gaat naar het archief met een `vervangen-door:`-link, de nieuwe krijgt een `vervangt:`-regel. `/project-memory:memory-export-adr` zet die geschiedenis om in genummerde Architecture Decision Records in `docs/adr/`, inclusief status Geaccepteerd of Vervangen.

## Beperkingen

Topic-matching is trefwoord-gebaseerd, geen embeddings. In het pull-model weegt dat minder zwaar: de hint hoeft alleen het juiste bestand aan te wijzen, Claude beoordeelt de inhoud zelf. De Stop-triage vangt alleen expliciet gemarkeerde uitspraken; gebruik `/project-memory:memory-save` of schrijf "onthoud:" in je prompt voor alles wat zeker bewaard moet blijven. V0.1-opslag (categoriebestanden in de root van de memory-map) wordt bij het eerste gebruik automatisch naar `topics/` gemigreerd.
