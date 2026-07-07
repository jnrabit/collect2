"""Synchroner Ollama-Client für Agenten-Handler.

Bewusst blockierend mit hartem Timeout statt async: der async-Pfad
(`run_until_complete` aus Listener-Threads) hing im Alt-System stundenlang.
Jeder Agent läuft in seinem eigenen Bus-Thread — Blockieren ist dort ok.
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

from collect.config import settings

logger = logging.getLogger(__name__)

# ChatML-Turn-Marker (qwen/Yi/…): das lokale qwen2.5-Modelfile deklariert KEINE
# PARAMETER stop → ohne explizites stop läuft die Generierung über die Turn-
# Grenze hinaus und leakt `<|im_start|>user`, halluzinierte Fake-Runden und den
# Prompt-Schwanz. Wir setzen stop selbst; sanitize_completion ist Defense-in-Depth.
_STOP_SEQUENCES = ["<|im_start|>", "<|im_end|>", "<|endoftext|>"]
_NUM_PREDICT_CAP = 1024  # Sicherheitsnetz gegen Weglaufen (Antworten sind kürzer)


def _base_options(temperature: float) -> dict:
    return {
        "temperature": temperature,
        "stop": _STOP_SEQUENCES,
        "num_predict": _NUM_PREDICT_CAP,
    }


_CHATML_MARKERS = ("<|im_start|>", "<|im_end|>", "<|endoftext|>")


def _first_marker_index(text: str) -> int:
    """Position des frühesten ChatML-Markers, sonst -1."""
    idx = -1
    for marker in _CHATML_MARKERS:
        i = text.find(marker)
        if i != -1:
            idx = i if idx == -1 else min(idx, i)
    return idx


def sanitize_completion(text: str) -> str:
    """Schneidet ab dem ersten ChatML-Marker ab + trimmt Rand-Whitespace.
    Für Einmal-Antworten (nicht streamend — dort mid-stream-Whitespace-neutral)."""
    idx = _first_marker_index(text)
    return (text if idx == -1 else text[:idx]).strip()


def generate(prompt: str, system: str = "", model: Optional[str] = None,
             timeout: Optional[float] = None, temperature: float = 0.1,
             fmt: Optional[dict] = None) -> str:
    """Nicht-streamender Generate-Call. Wirft bei HTTP-/Timeout-Fehlern."""
    payload = {
        "model": model or settings.main_model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "keep_alive": "30m",
        "options": _base_options(temperature),
    }
    if system:
        payload["system"] = system
    if fmt:
        # JSON-Modus: BEWUSST ohne stop-Sequenzen (die ChatML-Marker kommen in
        # validem JSON nicht vor, und ein stop mitten im JSON würde es
        # zerreißen) + höheres num_predict (2048), da lange JSONs sonst
        # abgeschnitten würden. Ollamas format-Grammar erzwingt den Abschluss.
        payload["options"] = {"temperature": temperature, "num_predict": 2048}
    resp = requests.post(
        f"{settings.ollama_url}/api/generate",
        json=payload,
        timeout=timeout or settings.llm_timeout,
    )
    resp.raise_for_status()
    return sanitize_completion(resp.json().get("response", ""))


def generate_streaming(prompt: str, system: str = "", model: Optional[str] = None,
                       timeout: Optional[float] = None, temperature: float = 0.1,
                       on_token=None) -> tuple[str, dict]:
    """Streamender Generate-Call: on_token(delta) pro Chunk, → (text, stats).

    stats aus Ollamas Final-Chunk: eval_count (generierte Tokens) und
    tok_per_s (eval_count/eval_duration). Der timeout gilt pro Chunk-Read —
    ein aktiv streamendes Modell läuft also nie in den Timeout.
    """
    import json as _json

    payload = {
        "model": model or settings.main_model,
        "prompt": prompt,
        "stream": True,
        "think": False,
        "keep_alive": "30m",
        "options": _base_options(temperature),
    }
    if system:
        payload["system"] = system

    parts: list[str] = []
    emitted_len = 0  # Länge der bereits ausgegebenen Zeichen (für Marker-Check)
    stats: dict = {}
    stopped = False  # ChatML-Marker gesehen → weitere Deltas verwerfen
    with requests.post(f"{settings.ollama_url}/api/generate", json=payload,
                       stream=True, timeout=timeout or settings.llm_timeout) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line:
                continue
            try:
                chunk = _json.loads(line)
            except ValueError:
                continue
            delta = chunk.get("response", "")
            if delta and not stopped:
                combined = "".join(parts) + delta
                mi = _first_marker_index(combined)
                if mi != -1:
                    # Marker (ggf. über Chunk-Grenze zusammengesetzt) → bis dahin
                    # ausgeben, dann Schluss. Kein rstrip mid-stream.
                    keep = combined[emitted_len:mi]
                    stopped = True
                else:
                    keep = delta
                if keep:
                    parts.append(keep)
                    emitted_len += len(keep)
                    if on_token is not None:
                        # Callback-Fehler (z.B. transienter Bus-/Publish-Fehler)
                        # darf die Generierung nicht abbrechen — sonst ist der
                        # ganze bereits berechnete Prompt verschwendet.
                        try:
                            on_token(keep)
                        except Exception:
                            logger.warning("on_token-Callback fehlgeschlagen",
                                           exc_info=True)
            if chunk.get("done"):
                ec = chunk.get("eval_count")
                ed = chunk.get("eval_duration")
                stats = {
                    "eval_count": ec,
                    "tok_per_s": round(ec / (ed / 1e9), 1) if ec and ed else None,
                }
    return "".join(parts).strip(), stats
