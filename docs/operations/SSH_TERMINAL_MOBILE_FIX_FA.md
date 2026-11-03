# SSH terminal mobile/WebSocket repair

## What this patch changes

- Adds a dedicated Nginx `location ^~ /api/terminal/` with HTTP/1.1 Upgrade headers, disabled proxy buffering, and a long read timeout. The existing generated Nginx site did not forward the WebSocket `Upgrade` / `Connection` headers; normal API `GET` requests could still return `200` while the browser terminal failed with close code `1006`.
- Makes the terminal authenticate either with the existing HttpOnly web session + CSRF proof or with a fresh, signed Telegram Mini App `initData` frame. The signed data is sent in the first WebSocket message, never in the URL.
- Keeps same-origin validation. If `SONAR_WEB_ORIGIN` is configured, it must match the public panel origin. If it is not configured, the endpoint validates the `Origin` against the request host and forwarded scheme.
- Synchronizes a newly refreshed CSRF token with the main web panel.
- Uses an English POSIX locale (`LANG=C`, `LC_ALL=C`) and an English shell prompt for the current SSH terminal session only.
- Changes terminal controls, connection states, and terminal errors to English.
- Adds a full-screen mobile terminal layout and a fallback for Telegram Android WebViews that do not implement `HTMLDialogElement.showModal()`.

## Apply to an existing installation

1. Extract the patch into the project directory and overwrite the included files.
2. Apply the Nginx WebSocket location to the active managed site:

   ```bash
   cd /opt/radar-sonar
   sudo bash scripts/repair_terminal_proxy.sh
   ```

   The script backs up `/etc/nginx/sites-available/sonar-radar.conf`, adds or replaces only the `/api/terminal/` location, runs `nginx -t`, and reloads Nginx. If Nginx validation fails, it restores the backup.

3. Restart the web API:

   ```bash
   sudo systemctl restart sonar-web
   sudo systemctl status sonar-web --no-pager -l
   ```

4. Open the panel again on mobile. In Telegram, close the Mini App completely and reopen it to create a fresh WebView session.

## Verify the WebSocket path

Watch the web service while opening the terminal:

```bash
sudo journalctl -u sonar-web -f
```

A successful upgrade should show a WebSocket route/accept event rather than an ordinary HTTP `GET /api/terminal/...` returning `404` or `403`.

If the browser still reports `1006`, check the public URL and origin configuration:

```bash
sudo grep -E '^SONAR_WEB_ORIGIN=|^SONAR_WEB_PORT=' /etc/sonar-bot.env
sudo nginx -T | grep -A18 -B2 'location \^~ /api/terminal/'
```

`SONAR_WEB_ORIGIN` must equal the panel's public origin, including `https://` and any non-standard port, with no path suffix.

## About the event-loop lag in the supplied bot log

`EVENT LOOP LAG: 76.42s` is emitted by `sonar-bot`, not `sonar-web`. It is independent of the browser WebSocket handshake. The later `sonar-web` API requests returning `200 OK` show that those HTTP endpoints responded, but they do not prove that the WebSocket upgrade worked.

The supplied lines do not identify which synchronous call held the bot event loop. Do not increase `SONAR_EXECUTOR_WORKERS` blindly: the log already reports 50 threads and does not identify executor starvation. If the lag repeats after the WebSocket repair, capture a Python stack dump at the time of the stall (for example with `py-spy dump --pid $(systemctl show -p MainPID --value sonar-bot)`) before changing scheduler concurrency.
