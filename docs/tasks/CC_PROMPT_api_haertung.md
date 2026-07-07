# CC-Auftrag: collect2 — API-Härtung vor Exposure

## Arbeitsmodus (verbindlich)

**Zwei Phasen. Erst PLAN, dann STOP.** Du erstellst zuerst einen vollständigen
Umsetzungsplan (siehe §Planformat) und wartest auf mein explizites „go".
Vor dem „go" wird **keine Datei geschrieben, kein Commit gemacht.** Lesen,
Analysieren, Threat-Model-Skizze sind erlaubt und erwünscht.

Nach „go": in der geplanten Reihenfolge abarbeiten, nach jedem sinnvollen
Block kurzer Ist-Bericht.

## Kontext

Repo: `~/collect2` (privates Remote `jnrabit/collect2`, CI grün, 196 Tests).
Systemstand in `AUDIT.md`, Design-Anker `DESIGN.md` — **beide zuerst lesen.**

Betroffen: `src/collect/api.py` (REST-API, `collect-api`-CLI) und die
Web-Chat-Oberfläche unter `http://127.0.0.1:8767/chat`.

**Ist-Zustand (AUDIT §7, §9):** API bewusst localhost-only, **ohne Auth**.
Executor-Grenzen existieren (Lesen nur unter `~/collect2`+`~/collect`,
Schreiben nur im Workspace, `execute:` default aus). `validation.py` blockt
Security-Patterns bei Writes.

**Threat-Model (wichtig, bestimmt den Umfang):** Ein-Operator-System,
lokal-first. Ziel ist **nicht** Multi-Tenant-User-Management, sondern:
die API so härten, dass sie einen Reverse-Proxy-/Tunnel-Exposure-Schritt
*überleben* könnte, ohne dass ein zweiter Aufwand nötig wird. Proportional
bleiben — kein OAuth-Server, keine User-DB für einen einzelnen Nutzer.

Geltende Regeln aus DESIGN §5, nicht verhandelbar:
- Module < 400 LOC, Logik von Transport getrennt (ohne Redis testbar)
- Kein Blocking-Wait
- Anti-Scaffold: die API ist ein existierender Consumer — Härtung ist
  legitim; aber nichts bauen, was über das Threat-Model hinausschießt
- `collect-doctor --fast` + Pre-Commit-Guard nach jedem Block grün
- Referenz-Altsysteme `~/collect`, `~/vibelike` read-only, nie verändern

**Default bleibt sicher:** localhost-Bind bleibt Standard. Exposure ist
immer ein bewusster, dokumentierter Opt-in-Schritt — dieser Auftrag baut
die Schutzschicht, aktiviert aber keine Exposure.

---

## Härtungs-Baustellen

### 1 — Authentifizierung
- Statischer Bearer-Token / API-Key aus `.env` (`COLLECT_API_TOKEN`),
  konstant-zeit-Vergleich gegen Timing-Leaks. Kein Token gesetzt →
  **entweder** API verweigert den Start bei Nicht-localhost-Bind **oder**
  läuft offen nur auf localhost (deine Empfehlung im Plan begründen).
- Auth als FastAPI-Dependency, sauber testbar ohne echten Server
  (TestClient). Ausgenommen: `/health`-artiger Liveness-Endpoint darf offen
  bleiben (im Plan benennen, welche Endpoints öffentlich sind).
- Web-Chat-Oberfläche: wie kommt der Token dort rein, ohne ihn ins HTML zu
  hardcoden? (Header vom Client, Session, oder localhost-Ausnahme — Optionen
  im Plan abwägen.)

### 2 — Rate-Limiting
- Pro-Token bzw. pro-IP-Limit auf die teuren Endpoints (Query/Retrieval,
  Workflow). Leichtgewichtig, in-process (kein Redis-Zwang, da API auch
  ohne Redis-Bus laufbar sein soll — prüfen, ob das hier zutrifft).
- Getrennte Limits: teure LLM-Endpoints strenger als leichte Status-Reads.
- Antwort bei Überschreitung: sauberes 429 mit `Retry-After`, kein Stacktrace.

### 3 — Request-Hygiene & CORS
- CORS: aktuellen Zustand prüfen (offen? localhost?) und auf explizite
  Allowlist setzen (default nur localhost-Origin).
- Request-Size-Limit (Body-Cap) gegen versehentliche/absichtliche Riesen-
  Payloads. Query-Längen-Limit.
- Security-Header (nüchtern, kein Cargo-Cult): `X-Content-Type-Options`,
  minimale sinnvolle Auswahl — im Plan begründen, welche und warum.

### 4 — Fehler- & Leak-Hygiene
- Keine internen Details in Fehler-Responses (Pfade, Stacktraces, Modell-
  Namen, Vault-Interna). Generische Client-Fehler, Details nur ins Log.
- Auth-Fehlschläge geloggt (Zeitpunkt, Endpoint, IP) für spätere Sichtbarkeit
  — aber ohne den versuchten Token zu protokollieren.

### 5 — Exposure-Schutzschalter (dokumentiert, inaktiv)
- Eine klare `.env`-Option, die Nicht-localhost-Bind erlaubt
  (z. B. `COLLECT_API_BIND`), mit Preflight: Bind ≠ localhost **und** kein
  Token gesetzt → Start verweigern mit klarer Meldung.
- `.env.example` + kurzer Abschnitt in `AUDIT.md`/DESIGN: „Was vor echter
  Exposure noch zu tun ist" (TLS-Terminierung liegt beim Reverse-Proxy,
  nicht in der App — als Grenze klar benennen).

---

## Nicht-Ziele (bewusst)
Multi-User/Rollen, OAuth/OIDC, User-Datenbank, Session-Management über
Token hinaus, TLS in der App selbst, WAF-artige Filter, API-Versionierung.
Wenn dir beim Planen etwas davon nötig erscheint, benenne es als Frage —
bau es nicht ungefragt.

---

## Planformat (Phase 1 — davor kein „go")

1. **Ist-Analyse** — aktuelle `api.py` sichten: welche Endpoints existieren,
   welcher hat welche Wirkung/Kosten, wo läuft was ungeschützt. CORS-Ist-Zustand.
   Läuft die API mit oder ohne Redis? (bestimmt Rate-Limit-Ansatz)
2. **Threat-Model kurz** — was ändert sich konkret zwischen „localhost heute"
   und „Tunnel-exponiert morgen", welche Baustelle deckt welches Risiko.
3. **Schritte** — konkrete Dateien (neu/geändert) mit geschätzten LOC,
   Reihenfolge, Module-Limit 400 beachten. Wo eigenes Modul (z. B.
   `api_security.py` / `auth.py`) statt api.py aufblähen?
4. **Tests** — neue Tests (Auth erzwungen, 401/429-Pfade, offener Health-
   Endpoint, CORS-Rejection) via TestClient ohne echten Server; welche der
   196 bestehenden betroffen.
5. **Risiken & Rollback** — was kann den Web-Chat oder die CLIs brechen,
   wie zurück. (Der Web-Chat ist der wahrscheinlichste Kollateralschaden.)
6. **Offene Entscheidungen** — als konkrete Fragen mit deiner Empfehlung +
   Begründung. Mindestens: Auth-Verhalten bei fehlendem Token; Web-Chat-
   Token-Weg; Rate-Limit-Mechanik mit/ohne Redis.

Dann: **STOP. Warten auf „go".**
