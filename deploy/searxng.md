# SearXNG — Backend für die Web-Recherche

collect2s Web-Suche (`search/web.py`) spricht eine selbst-gehostete SearXNG-
Instanz auf `http://localhost:8888` an (`COLLECT_WEB_SEARCH_URL`). Kein
Cloud-Key, lokal-first.

## Aufsetzen (Docker)

```bash
mkdir -p ~/searxng-config
# settings.yml (JSON-Format AKTIVIEREN — sonst liefert SearXNG nur HTML):
cat > ~/searxng-config/settings.yml <<'EOF'
use_default_settings: true
server:
  secret_key: "<openssl rand -hex 32>"
  bind_address: "127.0.0.1"
  limiter: false
  image_proxy: false
search:
  formats:
    - html
    - json          # collect2-Adapter nutzt format=json
  safe_search: 1
EOF

docker run -d --name searxng --restart unless-stopped \
  --network host \
  -e SEARXNG_PORT=8888 \
  -e GRANIAN_HOST=127.0.0.1 \
  -v ~/searxng-config/settings.yml:/etc/searxng/settings.yml:ro \
  searxng/searxng
```

## Warum diese (nicht-offensichtlichen) Flags

- **`--network host`**: firewalld auf Nobara/Fedora blockt den Egress der
  Docker-Bridge → Container kann keine Suchmaschinen erreichen (`Temporary
  failure in name resolution`). Host-Networking umgeht das ohne `sudo
  firewall-cmd`. Alternative (sauberer, braucht sudo): Bridge + `-p
  127.0.0.1:8888:8080` + `firewall-cmd --add-masquerade`.
- **`-e GRANIAN_HOST=127.0.0.1`**: der granian-Server bindet sonst `::` (alle
  Interfaces → LAN-exponiert). Das steuert NICHT die settings.yml, sondern
  diese Env-Var. Ergebnis: `LISTEN 127.0.0.1:8888` — nur localhost.
- **settings.yml read-only gemountet**: der Container chownt sonst das ganze
  Mount-Verzeichnis auf seine UID (`chrony`), danach ist es nicht mehr
  editierbar. Read-only-Einzeldatei behält der Operator.

## Verifikation

```bash
ss -tlnp | grep :8888                       # LISTEN 127.0.0.1:8888 (nicht *:8888)
curl -s "http://localhost:8888/search?q=test&format=json" | jq '.results | length'
```

## Betrieb

- `--restart unless-stopped` + Docker boot-enabled → kommt nach Reboot wieder.
- Auto-Web bei Vault-FALLBACK ist per Default **aus**
  (`COLLECT_WEB_SEARCH_AUTO=false`); einschalten via `.env`. Der explizite
  Trigger („recherchiere im web nach …") funktioniert unabhängig davon.
- Logs: `docker logs searxng`. Nach settings-Änderung: `docker restart searxng`.
