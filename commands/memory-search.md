---
description: Doorzoek de projectmemory op trefwoorden
argument-hint: <zoektermen>
allowed-tools: Bash(python3:*)
---

Doorzoek de projectmemory van dit project op: $ARGUMENTS

Voer uit:

```
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/prompt_submit.py" --search "$ARGUMENTS"
```

Vat de gevonden entries kort samen voor de gebruiker. Zijn er geen matches, zeg dat dan en stel voor om met /project-memory:memory-save iets vast te leggen.
