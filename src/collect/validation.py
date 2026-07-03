"""Security-Scan für den Executor-Write-Gate — validator2-Kern aus vibelike.

Bewusst NUR die Security-Patterns portiert (der volle validator2 mit Plan-
Drift-, Docker- und Cross-File-Checks ist an vibelikes Code-Workflow gebunden;
Vollport erst, wenn collect2 einen Code-Generierungs-Workflow hat — DESIGN §4).

Severity high → blockt den Write; medium/low → Warnungen im Schritt-Ergebnis.
"""

from __future__ import annotations

import re

# (pattern, severity, check_id, message) — aus vibelike validator2._SECURITY_PATTERNS
SECURITY_PATTERNS = [
    (r"\beval\s*\(", "high", "eval_rce", "eval() — RCE-Risiko"),
    (r"\bexec\s*\(", "high", "exec_rce", "exec() — RCE-Risiko"),
    (r"shell\s*=\s*True", "medium", "subprocess_shell", "subprocess shell=True — Command Injection"),
    (r"pickle\.loads?\s*\(", "medium", "pickle_unsafe", "pickle.load — RCE bei untrusted input"),
    (r"yaml\.load\s*\(\s*[^,)]+\)", "medium", "yaml_unsafe", "yaml.load ohne Loader — nutze yaml.safe_load"),
    (r"(?i)\b(password|secret|api_key|token)\s*=\s*[\"'][A-Za-z0-9_\-]{8,}[\"']",
     "high", "hardcoded_cred", "hardcoded credential"),
    (r"verify\s*=\s*False", "medium", "ssl_disabled", "TLS-Verification deaktiviert"),
    (r'\bcursor\.execute\s*\(.*f"[^"]*\{[^}]*\}[^"]*"', "high", "sql_fstring",
     "f-String in SQL-Query — SQL-Injection-Risiko"),
    (r"\btempfile\.mktemp\s*\(", "medium", "tempfile_race", "tempfile.mktemp() — Race Condition"),
    (r"\bos\.chmod\s*\(.*0o?777", "medium", "chmod_777", "chmod 777 — zu permissiv"),
    (r"\bprivate_key\s*=\s*[\"'].*-----BEGIN", "high", "key_hardcoded", "Private Key hardcoded"),
]

_COMPILED = [(re.compile(p), sev, cid, msg) for p, sev, cid, msg in SECURITY_PATTERNS]


def scan_security(content: str, path: str = "?") -> list[dict]:
    """Zeilenweiser Scan. → [{severity, check, location, message}, …]"""
    findings = []
    for lineno, line in enumerate(content.splitlines(), 1):
        for pattern, severity, check_id, message in _COMPILED:
            if pattern.search(line):
                findings.append({
                    "severity": severity,
                    "check": check_id,
                    "location": f"{path}:{lineno}",
                    "message": message,
                })
    return findings


def write_blockers(content: str, path: str = "?") -> list[str]:
    """High-Severity-Findings, die einen Write blocken sollen."""
    return [f"{f['location']}: {f['message']}"
            for f in scan_security(content, path) if f["severity"] == "high"]
