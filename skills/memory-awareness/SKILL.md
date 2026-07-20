---
name: memory-awareness
description: Herken tijdens het werken zelf wanneer iets in de projectmemory hoort en sla het direct op. Gebruik deze skill wanneer de gebruiker een besluit neemt of bevestigt (ook impliciet, zonder signaalwoord), een aanname van jou corrigeert, een afspraak met een klant of teamlid noemt, iets expliciet buiten scope verklaart, of wanneer je tijdens debuggen een niet-triviale valkuil ontdekt die toekomstige sessies tijd bespaart.
---

# Zelf herkennen wat bewaard moet worden

De automatische triage vangt alleen uitspraken met signaalwoorden ("besluit:", "voortaan"). Jij leest het hele gesprek en herkent meer. Sla zelf op wanneer je een van deze momenten ziet:

1. **Impliciet besluit dat wordt uitgevoerd.** "Laten we dan maar TypeScript nemen" gevolgd door daadwerkelijk bouwen = een besluit, ook zonder het woord besluit. Topic: decisions.
2. **Correctie van jouw aanname.** Zegt de gebruiker "nee, wij doen dat altijd met X", dan is dat een conventie die je moet onthouden. Topic: conventions.
3. **Ontdekte valkuil.** Kost een bug of eigenaardigheid jullie meer dan een paar minuten en is hij niet-triviaal (versieconflict, stille API-limiet, verrassend gedrag), sla de conclusie op. Topic: gotchas.
4. **Klant- of scopefeit.** Afspraken, voorkeuren of expliciete uitsluitingen die in het gesprek voorbijkomen. Topic: klanten of scope-nee.

## Wanneer NIET opslaan

- Jouw eigen suggesties of opties die de gebruiker nog niet heeft omarmd; een voorstel is geen besluit.
- Hypothesen, brainstorms, tijdelijke debugstappen, triviale feiten.
- Twijfel je, vraag dan kort: "Zal ik dit vastleggen in de projectmemory?"

## Hoe opslaan

Gebruik exact dezelfde route als /project-memory:memory-save:

```
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/memlib.py" add --topic <topic> --title "<max 9 woorden>" --keywords "<3-6 trefwoorden>" --body "<zelfstandig leesbare zin of alinea>"
```

Deduplicatie, secret-scrubbing en conflictdetectie gelden automatisch; dubbel opslaan kan dus geen kwaad, het wordt geweigerd. Bevestig het in je antwoord met precies 1 korte regel, bijvoorbeeld: "(vastgelegd in memory: TypeScript als projecttaal)". Maximaal een paar keer per sessie; kwaliteit boven volledigheid.
