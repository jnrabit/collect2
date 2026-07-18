"""collect-vault — Management-CLI für die lokalen Vaults.

  collect-vault stats   [--vault general|code|all]   # Bestand, Lücken, Quellen
  collect-vault diff    [--since 7d]                 # neue Docs + Cache-Lücken
  collect-vault rebuild [--all] [--yes]              # fehlende Vektoren embedden
  collect-vault prune   [--yes]                      # Waisen-Vektoren entfernen
  collect-vault dedupe  [--json]                     # Duplikate REPORTEN

Grundsätze:
- dedupe LÖSCHT NIE — es reportet nur (Vault-Writes sind die sensibelste
  Operation; Entfernen bleibt ein bewusster, manueller Schritt).
- rebuild ist inkrementell (nur fehlende Vektoren) und damit resume-fähig;
  --all (kompletter Re-Embed, auf CPU STUNDEN bei 259k Docs) verlangt
  explizite Bestätigung und checkpointet alle 500 Docs.
- Embedding identisch zum Ingest: content, unnormalisiert, float32 —
  sonst wäre der Cache inkonsistent zum Bestand.
"""

from __future__ import annotations

import argparse
import json
import pickle
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from collect.config import settings

_CHECKPOINT_EVERY = 500


def _files(vault: str) -> tuple[Path, Path]:
    if vault == "code":
        return Path(settings.code_vault_file), Path(settings.code_cache_file)
    return Path(settings.knowledge_vault_file), Path(settings.knowledge_cache_file)


def _load(vault: str) -> tuple[list, dict]:
    from collect.retrieval.vault import Vault
    vault_file, cache_file = _files(vault)
    archive = Vault(vault_file).load() if vault_file.exists() else []
    cache = {}
    if cache_file.exists():
        with open(cache_file, "rb") as f:
            cache = pickle.load(f)
    return archive, cache


def _doc_ts(doc: dict) -> float | None:
    """Timestamp robust: Epoch-Zahl oder ISO-String, sonst None."""
    ts = doc.get("timestamp")
    if isinstance(ts, (int, float)) and ts > 0:
        return float(ts)
    if isinstance(ts, str):
        try:
            return time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            return None
    return None


def _gaps(archive: list, cache: dict) -> tuple[list, list]:
    """→ (docs ohne Vektor, Vektor-IDs ohne Doc)."""
    doc_ids = {str(d.get("id")) for d in archive}
    cache_ids = set(map(str, cache))
    missing = [d for d in archive if str(d.get("id")) not in cache_ids]
    orphans = sorted(cache_ids - doc_ids)
    return missing, orphans


def _mb(path: Path) -> str:
    return f"{path.stat().st_size / 1e6:.1f} MB" if path.exists() else "—"


# ── stats ─────────────────────────────────────────────────────────────────

def cmd_stats(vault: str) -> int:
    vaults = ["general", "code"] if vault == "all" else [vault]
    for v in vaults:
        vault_file, cache_file = _files(v)
        archive, cache = _load(v)
        missing, orphans = _gaps(archive, cache)
        print(f"═══ {v}-Vault ═══")
        print(f"  Dokumente: {len(archive):,}  ({vault_file.name}, {_mb(vault_file)})")
        print(f"  Vektoren:  {len(cache):,}  ({cache_file.name}, {_mb(cache_file)})")
        if cache_file.exists():
            age_d = (time.time() - cache_file.stat().st_mtime) / 86400
            print(f"  Cache-Alter: {age_d:.1f} Tage")
        if missing:
            print(f"  ⚠️  {len(missing)} Doc(s) OHNE Vektor → collect-vault rebuild")
        if orphans:
            print(f"  ⚠️  {len(orphans)} Vektor(en) ohne Doc (Waisen)")
        if not missing and not orphans:
            print("  ✓ Archiv und Cache konsistent")

        stamps = [t for d in archive if (t := _doc_ts(d)) is not None]
        if stamps:
            fmt = lambda t: time.strftime("%Y-%m-%d", time.localtime(t))
            print(f"  Zeitraum: {fmt(min(stamps))} … {fmt(max(stamps))} "
                  f"(neuestes Doc = letzter Harvest)")
        sources = Counter(str(d.get("source", "?"))[:40] for d in archive)
        for src, n in sources.most_common(6):
            print(f"    {n:>8,}  {src}")
        print()
    return 0


# ── diff ──────────────────────────────────────────────────────────────────

def _parse_since(s: str) -> float:
    m = re.fullmatch(r"(\d+)([dh])", s.strip())
    if not m:
        raise SystemExit(f"✗ --since erwartet z.B. '7d' oder '48h', nicht {s!r}")
    n, unit = int(m.group(1)), m.group(2)
    return n * (86400 if unit == "d" else 3600)


def cmd_diff(vault: str, since: str) -> int:
    archive, cache = _load(vault)
    missing, orphans = _gaps(archive, cache)
    cutoff = time.time() - _parse_since(since)

    recent = [(t, d) for d in archive
              if (t := _doc_ts(d)) is not None and t >= cutoff]
    recent.sort(reverse=True, key=lambda x: x[0])
    print(f"Neu in den letzten {since} ({vault}-Vault): {len(recent)} Doc(s)")
    by_source = defaultdict(int)
    for _, d in recent:
        by_source[str(d.get("source", "?"))[:40]] += 1
    for src, n in sorted(by_source.items(), key=lambda x: -x[1]):
        print(f"  {n:>6}  {src}")
    for t, d in recent[:30]:
        print(f"    {time.strftime('%m-%d %H:%M', time.localtime(t))}  "
              f"{str(d.get('title', '—'))[:60]}")
    if len(recent) > 30:
        print(f"    … und {len(recent) - 30} weitere")

    if missing:
        print(f"\n⚠️  {len(missing)} Doc(s) ohne Vektor (unsichtbar fürs "
              f"Retrieval!) → collect-vault rebuild")
        for d in missing[:10]:
            print(f"    {str(d.get('id'))[:30]}  {str(d.get('title', '—'))[:50]}")
    if orphans:
        print(f"⚠️  {len(orphans)} Waisen-Vektor(en) ohne Doc")
    return 0


# ── rebuild ───────────────────────────────────────────────────────────────

def _checkpoint(cache: dict, cache_file: Path) -> None:
    tmp = cache_file.with_suffix(cache_file.suffix + ".tmp")
    with open(tmp, "wb") as f:
        pickle.dump(cache, f)
    with open(tmp, "rb") as f:  # Verify vor dem Ersetzen
        if len(pickle.load(f)) != len(cache):
            raise RuntimeError("Checkpoint-Verify fehlgeschlagen")
    tmp.replace(cache_file)


def cmd_rebuild(vault: str, rebuild_all: bool, yes: bool) -> int:
    import numpy as np

    vault_file, cache_file = _files(vault)
    archive, cache = _load(vault)
    if not archive:
        print(f"✗ Kein Archiv: {vault_file}")
        return 1
    missing, _ = _gaps(archive, cache)
    targets = archive if rebuild_all else missing
    if not targets:
        print(f"✓ Nichts zu tun — alle {len(archive):,} Docs haben Vektoren.")
        return 0

    print(f"{'KOMPLETTER Re-Embed' if rebuild_all else 'Rebuild'}: "
          f"{len(targets):,} von {len(archive):,} Docs ({vault}-Vault)")
    if rebuild_all and not yes:
        est_h = len(targets) / 3600  # grob ~1 Doc/s auf CPU
        answer = input(f"⚠️  Das dauert auf CPU grob ~{est_h:.1f}h und ersetzt "
                       f"ALLE Vektoren. Fortfahren? [yes/N] ")
        if answer.strip().lower() != "yes":
            print("Abgebrochen.")
            return 1

    # Backup einmal am Start (wie ingest._safe_write)
    if cache_file.exists():
        bak = cache_file.with_suffix(cache_file.suffix + f".bak-{int(time.time())}")
        bak.write_bytes(cache_file.read_bytes())
        print(f"Backup: {bak.name}")

    from collect.retrieval.embedding import get_backend
    emb = get_backend()
    t0, done = time.time(), 0
    for doc in targets:
        # identisch zum Ingest: content, unnormalisiert, float32
        vec = emb.embed([str(doc.get("content", ""))], normalize=False)[0]
        cache[str(doc.get("id"))] = np.asarray(vec, dtype=np.float32)
        done += 1
        if done % 100 == 0 or done == len(targets):
            rate = done / max(time.time() - t0, 1e-9)
            eta = (len(targets) - done) / max(rate, 1e-9)
            print(f"  {done:,}/{len(targets):,}  ({rate:.1f} Docs/s, "
                  f"ETA {eta / 60:.0f}min)", flush=True)
        if done % _CHECKPOINT_EVERY == 0:
            _checkpoint(cache, cache_file)
    _checkpoint(cache, cache_file)
    print(f"✓ {done:,} Vektor(en) geschrieben → {cache_file.name}. "
          f"Service-Neustart nötig (RAM-Kopie).")
    return 0


# ── prune ─────────────────────────────────────────────────────────────────

def cmd_prune(vault: str, yes: bool) -> int:
    """Waisen-Vektoren entfernen (Cache-Keys ohne Doc im Archiv).

    Waisen sind nicht harmlos: sie stehen in der Such-Matrix, bestimmen
    best_distance/Zone mit — aber ihre Treffer werden beim Doc-Lookup
    verworfen. Ergebnis: Zonen-Urteile von Geistern, 0 sichtbare Treffer."""
    _, cache_file = _files(vault)
    archive, cache = _load(vault)
    _, orphans = _gaps(archive, cache)
    if not orphans:
        print(f"✓ Keine Waisen-Vektoren ({vault}-Vault, {len(cache):,} Vektoren).")
        return 0
    print(f"{len(orphans):,} Waisen-Vektor(en) von {len(cache):,} "
          f"({vault}-Vault) — z.B.: {', '.join(orphans[:3])}")
    if not yes:
        answer = input("Entfernen? (Backup wird angelegt) [yes/N] ")
        if answer.strip().lower() != "yes":
            print("Abgebrochen.")
            return 1
    bak = cache_file.with_suffix(cache_file.suffix + f".bak-{int(time.time())}")
    bak.write_bytes(cache_file.read_bytes())
    print(f"Backup: {bak.name}")
    for k in orphans:
        cache.pop(k, None)
    _checkpoint(cache, cache_file)
    print(f"✓ {len(orphans):,} entfernt → {len(cache):,} Vektoren. "
          f"Service-Neustart nötig (RAM-Kopie).")
    return 0


# ── dedupe ────────────────────────────────────────────────────────────────

def _near_hash(text: str) -> str:
    """Normalisierter Hash: Groß/klein, Whitespace, Satzzeichen egal."""
    import hashlib
    norm = re.sub(r"[^a-z0-9äöüß]+", " ", text.lower()).strip()
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


def cmd_dedupe(vault: str, as_json: bool) -> int:
    from collect.harvest.ingest import content_hash

    archive, _ = _load(vault)
    exact, near = defaultdict(list), defaultdict(list)
    for d in archive:
        content = str(d.get("content", ""))
        entry = {"id": str(d.get("id")), "title": str(d.get("title", ""))[:60],
                 "source": str(d.get("source", ""))[:40]}
        exact[content_hash(content)].append(entry)
        near[_near_hash(content)].append(entry)

    exact_groups = [g for g in exact.values() if len(g) > 1]
    exact_ids = {e["id"] for g in exact_groups for e in g}
    # Near-Gruppen ohne die exakten (sonst doppelt gemeldet)
    near_groups = [g for g in near.values() if len(g) > 1
                   and not all(e["id"] in exact_ids for e in g)]

    if as_json:
        print(json.dumps({"exact": exact_groups, "near": near_groups},
                         ensure_ascii=False, indent=2))
        return 0

    print(f"Dedupe-Report ({vault}-Vault, {len(archive):,} Docs) — "
          f"es wird NICHTS gelöscht:")
    print(f"  Exakte Duplikate: {len(exact_groups)} Gruppe(n), "
          f"{sum(len(g) - 1 for g in exact_groups)} überzählige(s) Doc(s)")
    print(f"  Near-Dupes (normalisiert): {len(near_groups)} weitere Gruppe(n)")
    for label, groups in (("EXAKT", exact_groups), ("NEAR", near_groups)):
        for g in groups[:10]:
            print(f"  [{label}] {len(g)}×  {g[0]['title']}")
            for e in g:
                print(f"       {e['id'][:40]}  ({e['source']})")
    if len(exact_groups) > 10 or len(near_groups) > 10:
        print("  … gekürzt — voller Report mit --json")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("stats", help="Bestand, Konsistenz, Quellen")
    p.add_argument("--vault", default="all", choices=["general", "code", "all"])

    p = sub.add_parser("diff", help="neue Docs + Cache-Lücken")
    p.add_argument("--vault", default="general", choices=["general", "code"])
    p.add_argument("--since", default="7d", help="Zeitfenster, z.B. 7d oder 48h")

    p = sub.add_parser("rebuild", help="fehlende Vektoren embedden")
    p.add_argument("--vault", default="general", choices=["general", "code"])
    p.add_argument("--all", action="store_true",
                   help="ALLE Vektoren neu (Stunden!) statt nur fehlende")
    p.add_argument("--yes", action="store_true", help="Bestätigung überspringen")

    p = sub.add_parser("prune", help="Waisen-Vektoren entfernen (mit Backup)")
    p.add_argument("--vault", default="general", choices=["general", "code"])
    p.add_argument("--yes", action="store_true", help="Bestätigung überspringen")

    p = sub.add_parser("dedupe", help="Duplikate reporten (löscht nie)")
    p.add_argument("--vault", default="general", choices=["general", "code"])
    p.add_argument("--json", action="store_true")

    args = ap.parse_args()
    if args.cmd == "stats":
        return cmd_stats(args.vault)
    if args.cmd == "diff":
        return cmd_diff(args.vault, args.since)
    if args.cmd == "rebuild":
        return cmd_rebuild(args.vault, args.all, args.yes)
    if args.cmd == "prune":
        return cmd_prune(args.vault, args.yes)
    return cmd_dedupe(args.vault, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
