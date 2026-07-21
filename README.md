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
| `/project-memory:memory-report` | HTML-dashboard: activiteit, tijdlijn, kosten, gebruik |
| `/project-memory:memory-recent [dagen]` | terminal-tijdlijn van recente activiteit |
| `/project-memory:memory-bootstrap` | start-memory uit de git-historie van een bestaand project |
| `/project-memory:memory-import <pad>` | destilleer een document of transcript naar entries |
| `/project-memory:memory-import-jira <key>` | destilleer blijvende kennis uit een Jira-project |
| `/project-memory:memory-asof <ref>` | tijdmachine: wat wisten we toen |
| `/project-memory:memory-patterns <map>` | terugkerende kennis over projecten heen |

## Installatie en configuratie

```
claude --plugin-dir /pad/naar/project-memory
```

Omgevingsvariabelen (winnen van elke config): `MEMORY_SCOPE`, `MEMORY_INJECTION`, `MEMORY_INDEX_BUDGET`, `MEMORY_RETRIEVAL_BUDGET`, `MEMORY_ARCHIVE_DAYS`, `MEMORY_MAX_ENTRIES`.

## Vrije documenten en ge-stemming (v0.13)

Een gewoon .md-bestand (handleiding, wiki-pagina) mag nu rechtstreeks in een topics-map worden gezet: heeft het geen entry-opmaak, dan gedraagt het zich als 1 doorzoekbaar document. Het telt mee in de index en de hints, zoekresultaten tonen het begin met een verwijzing naar het volledige bestand, en Claude leest het via het pull-model zelf. De consolidatie en verificatie laten zulke documenten met rust (de gebruiker beheert ze), entries eraan toevoegen wordt geweigerd met een duidelijke melding, en de validator ziet ze als normaal. Daarnaast kent de stemming nu het Nederlandse ge-voorvoegsel als matchvariant: "hoe wordt alles gehost" vindt een document vol "host" en "hosting". Onderweg is de hint-kwaliteit aangescherpt: NL-vraagwoorden (wat, hoe, welke) tellen niet meer mee als inhoud, een hint vereist een minimumscore van 3 zodat een los kort titelwoord geen hint meer triggert, en specifieke signalen (samenstellingen, titelwoorden van 5+ tekens, documentmatches) tellen volwaardig. Gemeten: gouden set 100% top-3, alle ruisvragen stil, relevante persoonshints blijven werken.

## Gezond-verstand-scope (v0.12.1)

Opslaan met "globaal" en terugzoeken zien nu altijd dezelfde wereld: heeft de globale store inhoud, dan doet hij standaard mee in index, hints en search, ook zonder dat er ooit een scope is ingesteld. Een expliciete keuze voor scope=project (in project- of globale config, of via env) blijft de opt-out. De search-melding toont voortaan waarin er gezocht is ("gezocht in: project, globaal") in plaats van de kale scope-waarde.

## Bootstrap voor bestaande projecten (v0.12)

`/project-memory:memory-bootstrap` bouwt een start-memory voor een project dat nooit een kickoff had, uit zijn eigen git-historie. Het script mijnt deterministisch: relevante commits (triviale zoals "fix typo" en "bump version" worden weggefilterd; commits met een body of besluit-achtige woorden krijgen voorrang, want daar staat de waarom), TODO/FIXME/HACK-comments met bestand en regelnummer, de README-kop en de release-tags. Claude destilleert dat in de sessie naar entries: commit-rationales worden decisions met bronvermelding van de hash, code-TODO's worden gotchas met het bestandspad als ref (zodat de dagelijkse verificatie er direct op werkt), README en tags worden context. Daarna vult de normale triage het geheugen verder aan, en werkt ook de tijdmachine over de geadopteerde historie.

## Zelf herkennen wat bewaard moet worden (v0.11)

Twee mechanismen bovenop de signaalwoord-triggers. Ten eerste herkennen de triggers nu ook impliciete besluiten ("laten we dan maar X nemen/gebruiken/kiezen", "dan doen we het met X"), werkwoord-beperkt zodat "laten we koffie halen" en "laten we hopen dat" niet triggeren; gemeten op de uitgebreide testset: 100% recall, 0 valse positieven. Ten tweede levert de plugin een skill mee (`memory-awareness`) die Claude in de sessie zelf leert herkennen wat bewaarwaard is, ook volledig zonder signaalwoord: impliciete besluiten die worden uitgevoerd, correcties van Claudes aannames, tijdens debuggen ontdekte valkuilen, en klant- of scopefeiten. Opslaan loopt via dezelfde CLI-route, dus scrubbing, dedupe en conflictdetectie gelden automatisch; de skill bevestigt elke opslag met 1 regel in het antwoord en slaat nooit eigen suggesties op als besluit. Twijfel wordt een korte vraag aan de gebruiker in plaats van een aanname.

## Dashboard en activiteitsoverzicht (v0.10)

Twee manieren om te zien wat er is gedaan, beide zonder server. `/project-memory:memory-recent [dagen]` geeft een terminal-tijdlijn: per dag de nieuwe entries, vervangen of verouderde besluiten, triage-resultaten en validatormeldingen. `/project-memory:memory-report` genereert het volledige HTML-dashboard (`.claude/memory/report.html`, zelfstandig bestand van ~11KB): kerncijfers, activiteit per week als grafiek (nieuwe entries versus reads door Claude), de tijdlijn met gebeurtenisbadges, de beslisgeschiedenis met vervangen-door-ketens, de topics-tabel met tokenkosten en leesfrequentie, en snoei-advies. Alle data komt uit wat het systeem toch al bijhoudt; er draait niets extra.

Het dashboard toont ook de **bespaarde tokens en de indicatieve euro-waarde** (30 dagen). De methodiek is bewust conservatief en transparant: de nulmeting is "alle actieve memory elke sessie volledig in context" (de CLAUDE.md-aanpak die je zonder plugin zou gebruiken, gemeten als aantal sessies maal totale actieve memory-tokens), en daar gaat het werkelijke verbruik vanaf (gelogde injecties plus de tokens van topicbestanden die Claude daadwerkelijk las). De euro-omrekening gebruikt `price_eur_per_mtok` (default 2.75, gebaseerd op het Sonnet input-tarief van $3/MTok; instelbaar per project of via env). Twee eerlijke kanttekeningen staan ook in het dashboard zelf: abonnementsgebruikers betalen per plan en niet per token, en prompt caching verlaagt de werkelijke kosten van de nulmeting, dus lees het getal als indicatie van vermeden contextballast, niet als factuurbedrag.

## Gemeten prestaties (v0.9.1)

Retrieval is getuned op metingen in plaats van gevoel, met `hooks/benchmark.py`: een gouden set van 26 realistische NL-vragen tegen een gevulde baseline plus kickoff-project. Resultaat met de default-config (hybrid): **100% top-3 hint-accuraatheid, 73% top-1**, met een hook-latency van ~5ms scoring en enkele tientallen ms end-to-end inclusief Python-opstart, onmerkbaar per prompt. Twee verbeteringen kwamen uit de metingen: Nederlandse samenstellingen matchen nu op hun kern (dagplanning vindt keyword "planning"), en de semantische drempel is backend-bewust (gemeten optimum 0.12 voor de hash-backend; modelbackends houden de ingestelde waarde). Pure semantiek zonder keywords bleek aantoonbaar zwakker dan de combinatie; de hybride default is dus geen aanname meer maar een meetuitslag. Draai de benchmark zelf (en breid de gouden set uit met eigen vragen) via `python3 hooks/benchmark.py --sweep`.

Daarnaast zijn vijf scenario-benchmarks gedraaid (v0.9.2). Triage: 87% recall op gemarkeerde uitspraken (impliciete besluiten zonder signaalwoord blijven een bewuste miss), 0 valse positieven na het verwijderen van de te brede "pas op"-trigger. Conflictdetectie: 100% op 10 paren inclusief gemene gevallen (JWT vs OAuth, Sentry vs Prometheus blijven terecht naast elkaar bestaan), dankzij een gemeten combinatieregel: matige similarity plus minstens 1 gedeeld trefwoord telt ook als conflict. Secret-scrubbing: 9/9 secretformaten gevangen, 0 valse positieven op onschuldige zinnen over wachtwoordbeleid en token buckets. Schaal: 1500 entries synchroniseren in 0.3s, semantisch zoeken in 27ms, budgetafkap werkt. Gelijktijdigheid: het verloren-update-probleem bij parallelle schrijvers (14/50 entries kwijt in de test) is opgelost met een exclusieve store-lock (fcntl) rond elke lees-wijzig-schrijf-cyclus; drie herhaalde runs met 2 parallelle schrijvers leveren nu 50/50 entries.

## Opslagintegriteit (v0.9)

De tekstopslag heeft nu database-garanties, zonder het git-reviewbare formaat op te geven:

**Atomair schrijven.** Elke schrijfactie gaat via tempbestand + rename (hetzelfde mechanisme dat databases gebruiken); een crash laat nooit een half topicbestand achter. Verweesde tempbestanden ruimt de consolidatie op.

**Round-trip-validatie.** Voor elke schrijfactie wordt het resultaat teruggeparsed en entry-voor-entry vergeleken; zou de parser ook maar 1 entry verliezen, dan wordt er niet geschreven en gelogd. Bodies worden bovendien gesaneerd zodat inhoud die zelf op het entry-formaat lijkt (een geciteerde header, een yaml-snippet die met "keywords:" begint) de parser niet kan misleiden. Stil dataverlies is daarmee technisch onmogelijk gemaakt.

**Strikte validator.** `memlib.py validate` (draait ook mee in elke consolidatie en in /memory-status) controleert elk topic- en archiefbestand op schema: parseerbaarheid, tekst buiten het entry-formaat (merge-conflictmarkers, handmatige edits, kapotte headers), duplicaten en ontbrekende keywords. Bekende beperking: losse tekst die exact achter de laatste entry wordt geplakt is niet te onderscheiden van een meerregelige body en wordt stilzwijgend onderdeel daarvan; dat soort wijzigingen is wel altijd zichtbaar in de git-diff.

## Agency-laag, tijdmachine en scope-bewaking (v0.8)

**Klant-scope.** Naast project en globaal is er een derde niveau: de klant-store (`~/.claude/project-memory/customers/<naam>/`), voor kennis die projecten van dezelfde klant overstijgt (contactpersonen, beslissnelheid, afspraken). Instellen per project met `memory-config --customer "Dakdekker BV"`; de index toont die entries als `[klant]` en opslaan kan via `--store customer` of "klant" bij /memory-save. Deel de customers-map desgewenst als git-repo, net als de globale baseline.

**Tijdmachine.** `/project-memory:memory-asof <commit|tag|2026-03>` reconstrueert via git wat het team op dat moment wist, schrijft de snapshot naar `.claude/memory/asof/` en toont expliciet wat er pas sindsdien is geleerd of vervangen. Vragen als "waarom is dit destijds zo gebouwd" worden beantwoord met de kennis van toen; wat er toen nog niet was, is meestal het echte antwoord.

**Patroondetectie.** `/project-memory:memory-patterns ~/werk` scant alle projectstores onder een map, clustert vergelijkbare entries over projecten heen (embeddings) en rapporteert kennis die in 2+ projecten terugkomt als promotiekandidaat voor de klant- of bedrijfsbaseline. Zo voedt de praktijk vanzelf je baseline.

**Negatieve memory.** Het topic `scope-nee` bewaakt wat expliciet niet moet. Uitspraken als "de klant wil geen app" of "buiten scope" worden automatisch getriageerd, en zodra een prompt die kant op beweegt injecteert de hook een WAARSCHUWING met de afspraak erbij, voordat er gebouwd wordt. Requirements zeggen wat wel; dit bewaakt wat nee was.

## Documenten importeren (v0.6)

`/project-memory:memory-import <pad> [doeltopic]` destilleert een document of gesprekstranscript naar memory-entries. Een voorbewerkingsscript herkent JSON/JSONL-transcripten, Teams/Zoom-ondertitels (.vtt/.srt, inclusief dedupe van rollende regels), Word (.docx, pure stdlib), PDF (via pdftotext of pypdf, met Read-fallback voor gescande PDF's), e-mail (.eml), HTML en platte tekst. Bij transcripten filtert het smalltalk en vulzinnen deterministisch weg (op een echt kickoff-transcript: van ~87k naar ~28k tokens), en splitst in leesbare chunks in `.claude/memory/imports/` (gitignored). Daarna leest Claude de chunks in de sessie zelf en extraheert requirements, besluiten, gotchas en klantcontext als losse entries, met bronvermelding en "(interpretatie)" bij onzekere transcriptiepassages. Opslag loopt via de normale route, dus scrubbing, deduplicatie en conflictdetectie gelden automatisch. Er is geen externe AI-dienst nodig: de destillatie gebeurt door de sessie die je toch al draait.

Retrieval kent sinds v0.6 ook lichte NL/EN-stemming, zodat "tekenen van daken" gewoon matcht op entries over "tekening" en "dak".

## Vangkwaliteit en meetlaag (v0.5)

**Direct opslaan.** Een prompt die begint met `onthoud:` of `remember:` wordt meteen opgeslagen, zonder op het einde van de sessie te wachten. Je krijgt directe bevestiging inclusief eventuele redacties of vervangingen.

**PreCompact-vangnet.** Vlak voordat Claude Code de context comprimeert, draait dezelfde triage als bij Stop. Beslissingen uit lange sessies gaan zo niet verloren bij compaction; deduplicatie voorkomt dubbele opslag als daarna ook de Stop-hook vuurt.

**Semantische conflictdetectie.** Naast de keyword-drempel (3 gedeelde trefwoorden) worden conflicten in decisions/conventions nu ook via cosine similarity gevonden, met een backend-bewuste drempel (`conflict_threshold`, default 0.8 voor modelbackends, automatisch begrensd op 0.5 voor de hash-backend).

**Eigen triggers.** Voeg bedrijfsjargon toe via de config zonder de plugin aan te passen:
```json
"triggers": [{"pattern": "(?i)\\bklantafspraak\\b", "topic": "klanten"}]
```

**Usage-tracking en rapport.** Een PostToolUse-hook registreert welke topicbestanden Claude echt leest (`.usage.jsonl`, blijft lokaal). De index sorteert topics op werkelijk gebruik, en `/project-memory:memory-report` genereert een statisch HTML-rapport met per store de omvang, tokenkosten, leesfrequentie (30/90 dagen) en snoei-advies. Geen server; het rapport is gewoon een bestand.

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
