"""K4N0N3-Integration — Qwythos-Modell-Adapter für collect2.

Stellt generate()/generate_streaming() mit derselben Signatur wie
collect.agents.ollama bereit, konfiguriert für das Qwythos-Modell.

Da auf dieser Hardware (AMD RX 7600, kein CUDA) kein In-Process-Loading
möglich ist, ruft der Adapter weiterhin Ollamas HTTP-API auf. Der Mehrwert
liegt in der K4N0N3-spezifischen Konfiguration (Model-Parameter, LoRA-Pfad,
Stop-Tokens, Chat-Template) und der einheitlichen generate_fn-Schnittstelle.

Sobald CUDA/ROCm-In-Process verfügbar ist, kann der Adapter auf
k4n0n3.ZeroFlushModel umgestellt werden, ohne dass die Agenten geändert
werden müssen.
"""

from collect.k4n0n3.adapter import generate, generate_streaming, preload_model

__all__ = ["generate", "generate_streaming", "preload_model"]
