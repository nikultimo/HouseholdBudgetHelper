# Operational Runbooks

## Duplicate process / `Conflict` error

**Symptom in logs:** `telegram.error.Conflict` — another `getUpdates` is active.

This happens when two instances of the bot share the same `TELEGRAM_TOKEN` (e.g. a Docker container plus a local `python bot.py`).

**Fix:**

```bash
docker compose ps
docker compose stop
ps aux | grep -E "python .*bot.py" | grep -v grep || true
```

Stop the duplicate (kill the local process or the container), then restart a single instance.

---

## Docker DNS — "Temporary failure in name resolution"

**Symptom:** Bot starts but cannot reach Telegram, OpenRouter, or Yandex Disk from inside the container. Logs show `Temporary failure in name resolution`.

**Cause:** The host's stub resolver (`127.0.0.53`) is not reachable from Docker bridge networks.

**Fix:** Add upstream DNS servers to `docker-compose.yml`:

```yaml
services:
  bot:
    dns:
      - 8.8.8.8
      - 1.1.1.1
```

Find your host's real upstream DNS with `resolvectl status`, then restart:

```bash
docker compose up -d
docker compose logs -f
```

---

## Telegram reachable on host, not in container (TLS handshake timeouts)

**Symptom:** TLS handshake or connect timeouts to `api.telegram.org` from inside the container, even though the host can reach Telegram normally.

**Cause:** Some VPN or proxy setups route Telegram through the host network namespace but break it for Docker bridge networks.

**Fix:** Switch to host networking in `docker-compose.yml`:

```yaml
services:
  bot:
    network_mode: host
```

Then restart Compose.

---

## Admin sync operations

- `/sync` re-downloads the Excel file from Yandex Disk and invalidates the reader cache. It only reports success after the download completes.
- `/download_excel` re-downloads the file and sends the refreshed `.xlsx` to the Telegram chat. It only sends the document after the download completes.
- `/restore N` restores backup #N and re-uploads to Yandex Disk. It only reports success after the upload completes.

---

## Startup: missing Excel file

If `BUDGET_FILE_PATH` does not exist on startup and the Yandex Disk download fails, the bot creates the file from `example/budget_example.xlsx` after template validation. The bot then starts in a degraded state — use the admin-only `/setup` command to populate real data and upload a proper workbook.
