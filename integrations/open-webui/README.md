# Open WebUI integration (optional)

Lets a chat assistant in [Open WebUI](https://openwebui.com) list vendors, run the verifier, start a local
extraction and summarize results, without vendor reports ever entering the chat. Only findings summaries do.

Two parts:

- **`soc2_service.py`**: a small local HTTP API (Python standard library only) on `127.0.0.1:8765`. Every request
  needs the bearer token in `.token`, which `start_service.sh` creates. It runs the same scripts you would
  run by hand.
- **`soc2_review_tool.py`**: an Open WebUI *Tool*. Paste it into Workspace → Tools, then set its valves:
  `SERVICE_URL` (from a Docker/OrbStack container use `http://host.docker.internal:8765`), `API_TOKEN` (the
  contents of `.token`) and `DEFAULT_MODEL`.

```bash
integrations/open-webui/start_service.sh    # creates a venv and token on first run, starts the service
integrations/open-webui/stop_service.sh
```

| Endpoint | What it does |
|---|---|
| `GET /health` | Liveness |
| `GET /vendors` | Vendor folders, their extractions, latest verification result |
| `POST /verify {vendor, extraction?, ccm?}` | Run the verifier (seconds) |
| `POST /extract {vendor, model, method}` | Background local extraction (`hybrid`, `hybrid-all` or `full`), then verify |
| `GET /jobs/<id>` | Job status and result |
| `GET /activity` | Work-activity metadata only: vendor names, counts, dates. Never findings |

Security notes: it binds to localhost by default (`SOC2_BIND`), uses a random token, and only accepts
vendor names that exist under `Vendors/` and model tags that look like model tags. On macOS, start it from
Terminal: background agents can't read protected folders like Desktop.

If your assistant has long-term memory, read [MEMORY_RULES.md](MEMORY_RULES.md): review findings should never
be saved to it.
