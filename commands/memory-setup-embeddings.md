---
description: Eenmalige setup van semantische embeddings (synoniemen begrijpen) - installeert alles automatisch
allowed-tools: Bash(python3:*)
---

Zet semantische embeddings aan voor deze machine. Voer uit en toon de voortgang aan de gebruiker:

```
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/setup_embeddings.py"
```

Dit maakt eenmalig een eigen venv aan, installeert sentence-transformers (honderden MB's, kan enkele minuten duren), downloadt het model, draait een proef en zet de globale config om. Meld daarna: zoeken en conflictdetectie begrijpen vanaf nu synoniemen ("gehost" vindt ook "VPS" en "hosting"); de per-prompt hints blijven bewust op het snelle keyword-pad. Faalt een stap, toon dan de melding van het script; er wordt dan niets gewijzigd en de plugin blijft gewoon werken op de hash-backend.
