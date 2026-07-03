#!/usr/bin/env python3
"""Daten-Migration: Vaults + Caches vom Alt-System nach ~/collect2/data KOPIEREN.

Kopiert (löscht NIE etwas im Alt-System):
  monolith_archive.monolith, monolith_embedding_cache.pkl,
  code_archive.monolith, code_embedding_cache.pkl, code_centroid.npy,
  resonance_field.pkl / code_resonance_field.pkl (falls vorhanden)

Verifiziert per SHA256. Nach der Migration: COLLECT_DATA_DIR in .env auf
das neue Verzeichnis setzen (macht dieses Skript mit --write-env).

Usage:
  .venv/bin/python scripts/migrate_data.py --dry-run
  .venv/bin/python scripts/migrate_data.py --write-env
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

SOURCE = Path.home() / "collect" / "data"
TARGET = Path.home() / "collect2" / "data"
ENV_FILE = Path.home() / "collect2" / ".env"

FILES = [
    ("monolith_archive.monolith", True),
    ("monolith_embedding_cache.pkl", True),
    ("code_archive.monolith", True),
    ("code_embedding_cache.pkl", True),
    ("code_centroid.npy", False),
    ("resonance_field.pkl", False),        # optional: wird sonst neu aufgebaut
    ("code_resonance_field.pkl", False),
]


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while data := f.read(chunk):
            h.update(data)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description="Vault-Daten nach collect2 kopieren")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--write-env", action="store_true",
                    help="COLLECT_DATA_DIR in .env auf das neue Verzeichnis setzen")
    args = ap.parse_args()

    copied = skipped = 0
    for name, required in FILES:
        src, dst = SOURCE / name, TARGET / name
        if not src.exists():
            if required:
                print(f"✗ FEHLT (erforderlich): {src}")
                return 1
            print(f"– übersprungen (optional, fehlt): {name}")
            continue
        size_mb = src.stat().st_size / 1e6
        if dst.exists() and dst.stat().st_size == src.stat().st_size \
                and sha256(dst) == sha256(src):
            print(f"= identisch, übersprungen: {name} ({size_mb:.1f} MB)")
            skipped += 1
            continue
        if args.dry_run:
            print(f"→ würde kopieren: {name} ({size_mb:.1f} MB)")
            continue
        TARGET.mkdir(parents=True, exist_ok=True)
        print(f"→ kopiere {name} ({size_mb:.1f} MB) …", end=" ", flush=True)
        shutil.copy2(src, dst)
        if sha256(dst) != sha256(src):
            print("✗ SHA256-MISMATCH")
            return 1
        print("✓ verifiziert")
        copied += 1

    print(f"\n{copied} kopiert, {skipped} bereits aktuell → {TARGET}")

    if args.write_env and not args.dry_run:
        line = f"COLLECT_DATA_DIR={TARGET}\n"
        content = ENV_FILE.read_text() if ENV_FILE.exists() else ""
        if "COLLECT_DATA_DIR=" in content:
            content = "\n".join(
                line.rstrip("\n") if entry.startswith("COLLECT_DATA_DIR=") else entry
                for entry in content.splitlines()) + "\n"
        else:
            content += line
        ENV_FILE.write_text(content)
        print(f"✓ {ENV_FILE}: COLLECT_DATA_DIR={TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
