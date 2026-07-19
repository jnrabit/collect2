"""Trace-Validator — prüft Traces durchs ECHTE Chat-Template (CPU-only).

Rendert jeden Trace mit `tokenizer.apply_chat_template(messages, tools=...)`
über den zum Inferenzmodell passenden Tokenizer (Config traces_tokenizer;
Qwythos = Qwen3.5). Nur der Tokenizer wird geladen, kein Modell.

Prüfungen: Grundschema, Template-Rendering (darf nicht werfen), Tool-Call-
Validität gegen tool_schema_hash, Token-Längenverteilung (p50/p90/p99 +
Anteil >1024/>2048 → entscheidet seq_len), Think ≤80 Tokens, Register (kein
Sie), Sprache Deutsch.

transformers ist eine OPTIONALE Dependency: fehlt sie, laufen alle Nicht-
Token-Checks weiter und die Token-Checks melden "übersprungen (transformers
fehlt)" statt zu brechen.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Optional

from collect.config import settings
from collect.traces.schema import valid_tool_names, validate_entry

# Grobe Deutsch-Heuristik: Anteil deutscher Stoppwörter
_DE_STOPWORDS = frozenset(
    "der die das den dem des ein eine einen einem einer und oder aber wenn "
    "weil dass ich du er sie es wir ihr nicht mit von zu auf in aus ist war "
    "sind wird wurde werden wie was wo wann warum welche für auch noch schon "
    "nur sich zur zum beim vom im am um durch bei nach vor über unter gegen "
    "ohne seit bis als dann haben hat hatte kann können muss müssen soll "
    "diese dieser dieses jede alle alles andere dabei damit deshalb".split())
# Sie-Register-Marker (Groß-Sie / Höflichkeitsformen)
_SIE_MARKERS = re.compile(
    r"\b(Sie|Ihnen|Ihre|Ihrer|Ihren|Ihrem|Ihres)\b")
_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)
THINK_MAX_TOKENS = 80


def detect_language(text: str) -> str:
    words = re.findall(r"[a-zäöüß]{3,}", text.lower()[:800])
    if not words:
        return "unknown"
    de = sum(1 for w in words if w in _DE_STOPWORDS)
    return "de" if de / len(words) >= 0.15 else "other"


def approx_tokens(text: str) -> int:
    """Grobe Token-Schätzung ohne Tokenizer (~1 Token je 4 Zeichen dt.)."""
    return max(1, round(len(text) / 4))


class TraceValidator:
    def __init__(self, traces_dir: Optional[Path] = None,
                 max_seq_len: Optional[int] = None,
                 tokenizer_name: Optional[str] = None):
        self.traces_dir = Path(traces_dir or settings.traces_dir)
        self.max_seq_len = max_seq_len or settings.traces_seq_len
        self.tokenizer_name = tokenizer_name or settings.traces_tokenizer
        self._tokenizer = None
        self._tok_error: Optional[str] = None

    # ── Tokenizer (optional) ─────────────────────────────────────────────

    def _get_tokenizer(self):
        if self._tokenizer is not None or self._tok_error is not None:
            return self._tokenizer
        try:
            from transformers import AutoTokenizer
        except ImportError:
            self._tok_error = "transformers nicht installiert (pip install transformers)"
            return None
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(
                self.tokenizer_name, trust_remote_code=True)
        except Exception as e:  # noqa: BLE001
            self._tok_error = f"Tokenizer '{self.tokenizer_name}' nicht ladbar: {str(e)[:120]}"
        return self._tokenizer

    def _render(self, messages: list[dict], tools: list[dict]) -> tuple[Optional[str], Optional[str]]:
        tok = self._get_tokenizer()
        if tok is None:
            return None, self._tok_error
        try:
            rendered = tok.apply_chat_template(
                messages, tools=tools or None,
                tokenize=False, add_generation_prompt=False)
            return rendered, None
        except Exception as e:  # noqa: BLE001
            return None, f"apply_chat_template wirft: {str(e)[:140]}"

    def _token_len(self, rendered: str) -> int:
        tok = self._get_tokenizer()
        return len(tok.encode(rendered)) if tok else approx_tokens(rendered)

    # ── Einzeltrace ──────────────────────────────────────────────────────

    def validate(self, trace: dict) -> dict:
        issues: list[dict] = []
        for msg in validate_entry(trace):
            issues.append({"kind": "schema", "detail": msg})

        messages = trace.get("messages", [])
        tools = trace.get("tools", [])
        issues += self._check_tool_calls(messages, trace)
        issues += self._check_think(messages)
        issues += self._check_register_language(messages)

        token_count = None
        rendered, rerr = self._render(messages, tools) if messages else (None, "keine messages")
        if rerr and self._tok_error:
            issues.append({"kind": "tokenizer_skip", "detail": self._tok_error})
        elif rerr:
            issues.append({"kind": "render", "detail": rerr})
        elif rendered is not None:
            token_count = self._token_len(rendered)
            if token_count > self.max_seq_len:
                issues.append({"kind": "token_limit",
                               "detail": f"{token_count} > {self.max_seq_len}"})

        # tokenizer_skip zählt nicht als Fehlschlag (optionale Dependency)
        hard = [i for i in issues if i["kind"] != "tokenizer_skip"]
        return {"ok": not hard, "issues": issues,
                "trace_id": trace.get("meta", {}).get("trace_id", "?"),
                "token_count": token_count}

    def _check_tool_calls(self, messages: list[dict], trace: dict) -> list[dict]:
        names = valid_tool_names()
        # Pflichtparameter je Tool aus dem Trace-tools-Feld (falls vorhanden)
        req = {}
        for t in trace.get("tools", []):
            fn = t.get("function", {})
            req[fn.get("name")] = set(fn.get("parameters", {}).get("required", []))
        issues = []
        for i, m in enumerate(messages):
            if m.get("role") != "assistant":
                continue
            for tc in m.get("tool_calls", []) or []:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                if name not in names:
                    issues.append({"kind": "tool_call", "detail": f"msg {i}: unbekanntes Tool {name!r}"})
                    continue
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        issues.append({"kind": "tool_call", "detail": f"msg {i}: arguments kein JSON"})
                        continue
                missing = req.get(name, set()) - set(args or {})
                if missing:
                    issues.append({"kind": "tool_call",
                                   "detail": f"msg {i}: {name} fehlen Pflichtparameter {sorted(missing)}"})
                invented = set(args or {}) - {
                    p for t in trace.get("tools", []) if t["function"]["name"] == name
                    for p in t["function"].get("parameters", {}).get("properties", {})}
                if invented:
                    issues.append({"kind": "tool_call",
                                   "detail": f"msg {i}: {name} erfundene Parameter {sorted(invented)}"})
        return issues

    def _check_think(self, messages: list[dict]) -> list[dict]:
        issues = []
        for i, m in enumerate(messages):
            if m.get("role") != "assistant":
                continue
            content = m.get("content", "") or ""
            match = _THINK_RE.search(content)
            think = match.group(1).strip() if match else ""
            if not think:
                continue
            n = self._token_len(think) if self._get_tokenizer() else approx_tokens(think)
            if n > THINK_MAX_TOKENS:
                issues.append({"kind": "think_length", "detail": f"msg {i}: think ~{n} > {THINK_MAX_TOKENS} tok"})
            if think.startswith("The ") or detect_language(think) == "other":
                issues.append({"kind": "think_language", "detail": f"msg {i}: think nicht deutsch"})
        return issues

    def _check_register_language(self, messages: list[dict]) -> list[dict]:
        issues = []
        for i, m in enumerate(messages):
            if m.get("role") != "assistant" or m.get("tool_calls"):
                continue
            content = _THINK_RE.sub("", m.get("content", "") or "").strip()
            if len(content) < 20:
                continue
            if detect_language(content) == "other":
                issues.append({"kind": "language", "detail": f"msg {i}: answer nicht deutsch"})
            if _SIE_MARKERS.search(content):
                issues.append({"kind": "register", "detail": f"msg {i}: Sie-Register in answer"})
        return issues

    # ── Batch + Report ───────────────────────────────────────────────────

    def validate_all(self, step_kind: Optional[str] = None) -> dict:
        t0 = time.perf_counter()
        traces = self._load()
        if step_kind:
            traces = [t for t in traces if t.get("meta", {}).get("step_kind") == step_kind]
        results = [self.validate(t) for t in traces]
        token_counts = [r["token_count"] for r in results if r["token_count"]]
        ok = sum(1 for r in results if r["ok"])
        synth = sum(1 for t in traces if t.get("meta", {}).get("synthetic"))
        report = {
            "total": len(results), "ok": ok, "failed": len(results) - ok,
            "synthetic": synth,
            "synthetic_share": round(synth / len(results), 3) if results else 0.0,
            "tokenizer": self.tokenizer_name,
            "tokenizer_error": self._tok_error,
            "length": self._histogram(token_counts),
            "duration_s": round(time.perf_counter() - t0, 2),
            "results": results,
        }
        return report

    @staticmethod
    def _histogram(counts: list[int]) -> dict:
        if not counts:
            return {"note": "keine Token-Zahlen (Tokenizer fehlt/Fehler)"}
        s = sorted(counts)
        pct = lambda p: s[min(len(s) - 1, int(p / 100 * len(s)))]  # noqa: E731
        return {
            "n": len(s), "p50": pct(50), "p90": pct(90), "p99": pct(99),
            "max": s[-1],
            "over_1024": sum(1 for c in s if c > 1024),
            "over_2048": sum(1 for c in s if c > 2048),
        }

    def _load(self) -> list[dict]:
        # Dedup über trace_id (die Pipeline schreibt denselben Trace mehrfach)
        from collect.traces.collector import TraceCollector
        return TraceCollector(base_dir=self.traces_dir).load_all()

    def _load_raw(self) -> list[dict]:
        out = []
        if not self.traces_dir.exists():
            return out
        for f in sorted(self.traces_dir.glob("*.jsonl")):
            if f.name == "outcomes.jsonl":
                continue
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return out


def print_report(report: dict) -> None:
    print(f"\nTraces: {report['total']}  |  valide {report['ok']}  |  "
          f"invalide {report['failed']}  |  synthetisch {report['synthetic']} "
          f"({report['synthetic_share']*100:.0f}%)")
    if report.get("tokenizer_error"):
        print(f"Tokenizer: {report['tokenizer']} — NICHT geladen: {report['tokenizer_error']}")
    else:
        print(f"Tokenizer: {report['tokenizer']}")
    h = report["length"]
    if "p50" in h:
        print(f"Längen (Token): p50={h['p50']} p90={h['p90']} p99={h['p99']} "
              f"max={h['max']}  |  >1024: {h['over_1024']}  >2048: {h['over_2048']}")
        seq = 1024 if h["over_1024"] == 0 else 2048
        print(f"→ Empfohlene seq_len: {seq}")
    else:
        print(f"Längen: {h.get('note')}")
    for r in report["results"]:
        if not r["ok"]:
            details = "; ".join(i["detail"] for i in r["issues"] if i["kind"] != "tokenizer_skip")
            print(f"  ✗ {r['trace_id']}: {details}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Validiert Trace-JSONL durchs echte Chat-Template.")
    ap.add_argument("--traces-dir", type=Path, default=None)
    ap.add_argument("--tokenizer", default=None, help="HF-Repo oder lokaler Pfad")
    ap.add_argument("--seq-len", type=int, default=None)
    ap.add_argument("--json", action="store_true", help="nur JSON-Report ausgeben")
    args = ap.parse_args()

    v = TraceValidator(traces_dir=args.traces_dir, max_seq_len=args.seq_len,
                       tokenizer_name=args.tokenizer)
    report = v.validate_all()

    out_path = v.traces_dir / "validation_report.json"
    try:
        v.traces_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    except Exception:  # noqa: BLE001
        pass

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)
        print(f"\nReport: {out_path}")
    sys.exit(0 if report["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
