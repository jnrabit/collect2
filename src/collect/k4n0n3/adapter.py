"""K4N0N3-Adapter — generate/generate_streaming via In-Process oder Ollama.

Signatur-kompatibel mit collect.agents.ollama. Nutzt bei verfügbarer GPU
K4N0N3's ZeroFlushModel für In-Process-Inferenz mit Layer-Offloading.
Fällt auf Ollama-HTTP zurück, wenn keine GPU verfügbar ist.

Bei gesetztem COLLECT_K4N0N3_ENABLED=true wird dieser Adapter als
generate_fn anstelle von ollama.generate injiziert (siehe runner.py).
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Optional

import torch

from collect.config import settings

logger = logging.getLogger(__name__)

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)

# Stop-Marker: NUR Rollen-/EOS-Tokens. <think>/</think> gehoeren hier
# ausdruecklich NICHT hinein — Qwythos ist ein Reasoning-Modell und oeffnet
# den Think-Block selbst. Als Stop-Marker wuerden sie die Generierung sofort
# abschneiden; als _sanitize-Marker wuerden sie den GEDANKENGANG zurueckgeben
# und die eigentliche Antwort verwerfen.
QWYTHOS_STOP = ["<|im_start|>", "<|im_end|>", "<|endoftext|>"]

_MODEL_MAP: dict[str, str] = {
    "pdurlej/qwythos-9b-claude-mythos-5-1m": "empero-ai/Qwythos-9B-Claude-Mythos-5-1M",
    "qwen2.5:3b": "Qwen/Qwen2.5-3B",
    "qwen2.5:7b": "Qwen/Qwen2.5-7B-Instruct",
}

# Layer-Budget je Modell (MB). Wichtig: das ist NUR das Layer-Budget —
# Embeddings + lm_head liegen zusaetzlich resident auf der GPU. Bei Qwythos
# sind das 3,79 GiB (Vokabular 248k, tie_word_embeddings=false); mit 6 GB
# Layer-Budget waeren 8 GB VRAM rechnerisch ueberschritten (gemessenes OOM).
_BUDGET_MB: dict[str, int] = {
    "empero-ai/Qwythos-9B-Claude-Mythos-5-1M": 2048,
}
_DEFAULT_BUDGET_MB = 3072

# Cache pro Modellname — ein globales Singleton lieferte sonst fuer
# k4n0n3_rewrite_model still das zuerst geladene Modell zurueck.
_k4models: dict[str, object] = {}
_k4model_lock = threading.Lock()


def _sanitize(text: str) -> str:
    """Rollen-Tokens abschneiden, Gedankengang entfernen.

    Bei einem Reasoning-Modell gewinnt alles nach dem LETZTEN </think>: das
    Chat-Template oeffnet <think> bereits im Prompt, die Generierung enthaelt
    also nur noch das schliessende Tag."""
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    text = _THINK_BLOCK.sub("", text)
    for marker in QWYTHOS_STOP:
        idx = text.find(marker)
        if idx != -1:
            text = text[:idx]
    return text.strip()


def _hf_name(model_name: str) -> str:
    return _MODEL_MAP.get(model_name, model_name)


def _has_gpu() -> bool:
    try:
        return torch.cuda.is_available()
    except Exception:
        return False


def _get_k4model(model_name: str) -> object | None:
    if not _has_gpu():
        return None
    hf_name = _hf_name(model_name)
    with _k4model_lock:
        if hf_name not in _k4models:
            try:
                from k4n0n3 import ZeroFlushModel, auto_vram_budget
                budget = _BUDGET_MB.get(
                    hf_name, min(auto_vram_budget(), _DEFAULT_BUDGET_MB))
                logger.info("K4N0N3 In-Process: %s (Layer-Budget %d MB)",
                            hf_name, budget)
                # dtype bewusst NICHT erzwungen: from_pretrained ignoriert
                # torch_dtype bei manchen Modellen (Qwythos bleibt bf16), und
                # ein erzwungenes fp16 kollidiert dann im Quantisierungspfad.
                _k4models[hf_name] = ZeroFlushModel(
                    hf_name,
                    vram_budget_mb=budget,
                    prefetch_depth=1,
                    verbose=False,
                )
            except Exception as e:
                logger.warning("K4N0N3 In-Process fehlgeschlagen (%s): %s",
                               hf_name, e)
                _k4models[hf_name] = None
    return _k4models.get(hf_name)


def _render_prompt(k4m, prompt: str, system: str) -> str:
    """Chat-Template anwenden, wenn das Modell eines hat.

    Ohne Template bekommt ein Instruct-/Reasoning-Modell einen nackten Text
    und antwortet im Fortsetzungsmodus statt als Assistent."""
    tok = getattr(k4m, "tokenizer", None)
    msgs = ([{"role": "system", "content": system}] if system else []) + \
           [{"role": "user", "content": prompt}]
    if tok is not None and getattr(tok, "chat_template", None):
        try:
            return tok.apply_chat_template(msgs, add_generation_prompt=True,
                                           tokenize=False)
        except Exception:  # noqa: BLE001 — Fallback unten
            pass
    return f"{system}\n\n{prompt}" if system else prompt


def _generate_in_process(k4m, prompt: str, system: str,
                         temperature: float) -> str:
    """ZeroFlushModel.generate() dekodiert die GESAMTE Sequenz, also inklusive
    Prompt. Der Prompt-Praefix muss deshalb hier abgeschnitten werden."""
    rendered = _render_prompt(k4m, prompt, system)
    kwargs = {"max_new_tokens": settings.llm_num_predict}
    if temperature and temperature > 0:
        kwargs.update(do_sample=True, temperature=temperature)
    else:
        kwargs["do_sample"] = False
    raw = k4m.generate(rendered, **kwargs)
    if raw.startswith(rendered):                      # exakter Praefix
        raw = raw[len(rendered):]
    else:                                             # Template-/Sondertokens
        tail = prompt[-60:]
        idx = raw.rfind(tail)
        if idx != -1:
            raw = raw[idx + len(tail):]
    return _sanitize(raw)


def _ollama_generate(prompt: str, system: str = "", model: Optional[str] = None,
                     timeout: Optional[float] = None, temperature: float = 0.1,
                     fmt: Optional[dict] = None) -> str:
    import requests

    model_name = model or settings.main_model
    opts: dict = {
        "temperature": temperature,
        "stop": list(QWYTHOS_STOP),
        "num_predict": settings.llm_num_predict,
        "num_ctx": settings.llm_num_ctx,
    }
    if fmt:
        opts = {"temperature": temperature, "num_predict": 2048,
                "num_ctx": settings.llm_num_ctx}
    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "keep_alive": "30m",
        "options": opts,
    }
    if system:
        payload["system"] = system
    if fmt:
        payload["format"] = fmt
    resp = requests.post(
        f"{settings.ollama_url}/api/generate",
        json=payload,
        timeout=timeout or settings.llm_timeout,
    )
    resp.raise_for_status()
    return _sanitize(resp.json().get("response", ""))


def _ollama_streaming(prompt: str, system: str = "", model: Optional[str] = None,
                      timeout: Optional[float] = None, temperature: float = 0.1,
                      on_token=None) -> tuple[str, dict]:
    import json as _json
    import requests

    model_name = model or settings.main_model
    opts = {
        "temperature": temperature,
        "stop": list(QWYTHOS_STOP),
        "num_predict": settings.llm_num_predict,
        "num_ctx": settings.llm_num_ctx,
    }
    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": True,
        "keep_alive": "30m",
        "options": opts,
    }
    if system:
        payload["system"] = system

    parts: list[str] = []
    stats: dict = {}
    stopped = False
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
                for marker in QWYTHOS_STOP:
                    if marker in combined:
                        stopped = True
                        break
                if not stopped:
                    parts.append(delta)
                    if on_token is not None:
                        try:
                            on_token(delta)
                        except Exception:
                            logger.warning("on_token-Callback fehlgeschlagen", exc_info=True)
            if chunk.get("done"):
                ec = chunk.get("eval_count")
                ed = chunk.get("eval_duration")
                stats = {
                    "eval_count": ec,
                    "tok_per_s": round(ec / (ed / 1e9), 1) if ec and ed else None,
                }
    return _sanitize("".join(parts)), stats


def generate(prompt: str, system: str = "", model: Optional[str] = None,
             timeout: Optional[float] = None, temperature: float = 0.1,
             fmt: Optional[dict] = None) -> str:
    model_name = model or settings.k4n0n3_model or settings.main_model
    k4m = _get_k4model(model_name)

    # fmt (JSON-Schema) kann der In-Process-Pfad nicht — dafuer zu Ollama.
    if k4m is not None and not fmt:
        try:
            return _generate_in_process(k4m, prompt, system, temperature)
        except Exception as e:  # noqa: BLE001 — Fallback ist der Sinn
            logger.warning("K4N0N3 generate fehlgeschlagen: %s", e)

    return _ollama_generate(prompt, system, model_name, timeout, temperature, fmt)


def generate_streaming(prompt: str, system: str = "", model: Optional[str] = None,
                       timeout: Optional[float] = None, temperature: float = 0.1,
                       on_token=None) -> tuple[str, dict]:
    model_name = model or settings.k4n0n3_model or settings.main_model
    k4m = _get_k4model(model_name)

    if k4m is not None:
        try:
            # In-Process streamt nicht: der Text kommt als ein Block. Der
            # Callback wird trotzdem bedient, damit Aufrufer gleich bleiben.
            text = _generate_in_process(k4m, prompt, system, temperature)
            if on_token is not None:
                try:
                    on_token(text)
                except Exception:  # noqa: BLE001
                    logger.warning("on_token-Callback fehlgeschlagen", exc_info=True)
            return text, {"eval_count": len(text.split()), "tok_per_s": None,
                          "streamed": False}
        except Exception as e:  # noqa: BLE001
            logger.warning("K4N0N3 streaming fehlgeschlagen: %s", e)

    return _ollama_streaming(prompt, system, model_name, timeout, temperature, on_token)


def preload_model(model_name: str | None = None) -> bool:
    """Optional: Modell vorab laden (z.B. beim Agent-Start)."""
    name = model_name or settings.k4n0n3_model or settings.main_model
    return _get_k4model(name) is not None
