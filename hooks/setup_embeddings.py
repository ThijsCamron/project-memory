#!/usr/bin/env python3
"""Eenmalige setup van echte semantische embeddings (sentence-transformers).

Doet alles automatisch, met zichtbare voortgang:
  1. eigen venv aanmaken op ~/.claude/project-memory/venv
     (geisoleerd: raakt het systeem-Python niet, geen PEP 668-gedoe)
  2. sentence-transformers installeren (eenmalig, honderden MB's incl. torch)
  3. het model downloaden en een proef-embedding draaien
  4. de globale config op embedding_backend=local zetten
  5. de vectorindexen laten herbouwen bij de volgende sync

Bewust een los command en geen hook: dit duurt minuten en hoort zichtbaar en
eenmalig te gebeuren, niet stiekem tijdens een sessiestart.

Gebruik: python3 setup_embeddings.py [--model all-MiniLM-L6-v2]
"""

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memlib  # noqa: E402

VENV_DIR = os.path.join(os.path.expanduser("~"), ".claude", "project-memory", "venv")


def venv_python() -> str:
    return os.path.join(VENV_DIR, "Scripts" if os.name == "nt" else "bin",
                        "python.exe" if os.name == "nt" else "python3")


def step(n, msg):
    print(f"[{n}/5] {msg}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="all-MiniLM-L6-v2")
    args = parser.parse_args()

    step(1, f"venv aanmaken op {VENV_DIR}")
    os.makedirs(os.path.dirname(VENV_DIR), exist_ok=True)
    if not os.path.isfile(venv_python()):
        r = subprocess.run([sys.executable, "-m", "venv", VENV_DIR])
        if r.returncode != 0:
            print("venv aanmaken faalde; is python3-venv geinstalleerd?")
            return 1
    else:
        print("      bestaat al, hergebruiken")

    step(2, "sentence-transformers installeren (eenmalig, kan minuten duren)")
    r = subprocess.run([venv_python(), "-m", "pip", "install", "--quiet",
                        "--upgrade", "pip", "sentence-transformers"])
    if r.returncode != 0:
        print("installatie faalde (netwerk/proxy?); niets gewijzigd, "
              "de plugin blijft gewoon op de hash-backend werken")
        return 1

    step(3, f"model {args.model} downloaden en proefdraaien")
    probe = (
        "from sentence_transformers import SentenceTransformer;"
        f"m=SentenceTransformer('{args.model}');"
        "v=m.encode(['proefzin over hosting en deployment']);"
        "print(len(v[0]))"
    )
    r = subprocess.run([venv_python(), "-c", probe], capture_output=True, text=True,
                       timeout=600)
    if r.returncode != 0:
        print("modeldownload of proef faalde:")
        print("  " + (r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "onbekend"))
        print("niets gewijzigd; probeer later opnieuw met "
              "/project-memory:memory-setup-embeddings")
        return 1
    dim = r.stdout.strip().splitlines()[-1]
    print(f"      ok, vectordimensie {dim}")

    step(4, "globale config op embedding_backend=local zetten")
    cfg_path = os.path.join(os.path.dirname(VENV_DIR), "config.json")
    cfg = {}
    if os.path.isfile(cfg_path):
        try:
            cfg = json.load(open(cfg_path))
        except Exception:
            cfg = {}
    cfg["embedding_backend"] = "local"
    cfg["embedding_model"] = args.model
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

    step(5, "klaar")
    print("\nSemantische embeddings staan aan voor deze machine. De vectorindex "
          "van elke store wordt bij de eerstvolgende sessie of consolidatie "
          "automatisch opnieuw opgebouwd met het model (oude hash-vectoren "
          "worden vervangen). Zoekopdrachten en conflictdetectie begrijpen "
          "vanaf nu synoniemen: 'gehost' vindt ook 'VPS' en 'hosting'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
