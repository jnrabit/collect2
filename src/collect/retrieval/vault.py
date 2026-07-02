"""Vault — verschlüsselter Dokumentenspeicher (LZMA + Chaos-XOR).

Format-kompatibel mit den Alt-Vaults (collect/vibelike, `*.monolith`): der
Keystream der logistischen Map ist byte-identisch zur numba-Referenz-
implementierung reimplementiert (verifiziert gegen beide Produktiv-Vaults,
258.873 + 1.469 Docs) — nur vektorisiert in numpy statt JIT, wodurch die
numba/LLVM-Dependency entfällt.
"""

from __future__ import annotations

import hashlib
import json
import lzma
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_COMPRESSION_FILTER = [{"id": lzma.FILTER_LZMA2, "preset": 9 | lzma.PRESET_EXTREME}]
_LOGISTIC_R = 3.999
DEFAULT_KEY = "MONOLITH_V7_ROOT_KEY"


def _password_seed(password: str) -> float:
    h = hashlib.sha256(password.encode()).hexdigest()
    return (int(h[:12], 16) / 1e15) % 1.0


def chaos_cipher(data: np.ndarray, seed: float) -> np.ndarray:
    """XOR-Cipher via logistische Map (symmetrisch: encrypt == decrypt).

    Muss float64 bleiben und exakt 3 Iterationen x = r*x*(1-x) rechnen —
    jede Abweichung ändert den Keystream und macht Alt-Vaults unlesbar.
    """
    n = len(data)
    x = (np.arange(n, dtype=np.float64) * 0.0000001 + seed) % 1.0
    x[x <= 0] = 0.12345
    for _ in range(3):
        x = _LOGISTIC_R * x * (1.0 - x)
    keystream = (x * 255).astype(np.uint8)
    return data ^ keystream


class Vault:
    """Speichert/lädt JSON-serialisierbare Listen als verschlüsselte Binärdatei."""

    def __init__(self, filepath: str | Path, password: str = DEFAULT_KEY):
        self.filepath = Path(filepath)
        self.seed = _password_seed(password)

    def save(self, data: list) -> None:
        raw = json.dumps(data, separators=(",", ":")).encode()
        compressed = lzma.compress(raw, format=lzma.FORMAT_RAW,
                                   filters=_COMPRESSION_FILTER)
        arr = np.frombuffer(compressed, dtype=np.uint8)
        encrypted = chaos_cipher(arr, self.seed)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.filepath.write_bytes(encrypted.tobytes())

    def load(self) -> list:
        if not self.filepath.exists():
            return []
        raw = self.filepath.read_bytes()
        if not raw:
            return []
        try:
            arr = np.frombuffer(raw, dtype=np.uint8)
            decrypted = chaos_cipher(arr, self.seed)
            decompressed = lzma.decompress(decrypted.tobytes(),
                                           format=lzma.FORMAT_RAW,
                                           filters=_COMPRESSION_FILTER)
            return json.loads(decompressed.decode())
        except Exception as e:
            logger.error("Vault load error (%s): %s", self.filepath, e)
            return []
