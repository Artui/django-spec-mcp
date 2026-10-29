# `job_status/` — long-running job + templated resource + SSE push

A minimal Django project that demonstrates the **resource template +
server-initiated SSE push** pattern: clients kick off a job via a
service tool, poll its status by URI (`jobs://{job_id}`), and
optionally subscribe to GET-side SSE to receive a push the moment the
job completes.

| Surface                        | What it does                                                      |
|--------------------------------|-------------------------------------------------------------------|
| `register_tool(ServiceSpec)`   | `jobs.start` — kicks off a job (a `time.sleep` stand-in for real work) and returns its id immediately; `duration_seconds` is a read bounded to 1–60. |
| `register_resource` (template) | `jobs://{job_id}` — read the latest status of a single job; the template's `job_id` is the spec's read, and an id naming no job is a missing resource. |
| `register_tool(SelectorSpec)`  | `jobs.list` — list all jobs, with the FilterSet's status filter and ordering, and pagination. |
| `MCPServer.notify`             | Pushes a `notifications/jobs/done` JSON-RPC frame on the session's SSE stream the moment a job finishes. |

Each operation is a `django_service_specs` spec declaring
`permissions=[Unrestricted()]`, deliberately open, and a `DataclassPresenter`
over `jobs/shapes.py`'s `JobOutput`.

For brevity the "long-running" work is `threading.Thread + time.sleep`.
A real project would dispatch to Celery / RQ / Dramatiq and let the
worker call back into `await server.notify(...)` when the job
transitions to `done`.

## Layout

```
job_status/
├── README.md
├── manage.py
├── job_status/             ← Django project package
│   ├── __init__.py
│   ├── settings.py
│   ├── urls.py
│   └── asgi.py
└── jobs/                   ← Django app
    ├── __init__.py
    ├── apps.py
    ├── models.py
    ├── shapes.py           ← the dataclass every surface presents a job through
    ├── services.py
    ├── selectors.py
    ├── mcp.py
    └── migrations/
        ├── __init__.py
        └── 0001_initial.py
```

## Run

```bash
cd examples/job_status
uv pip install -e "../.." "django-service-specs[filter]"
python manage.py migrate

# ASGI server so SSE works; uvicorn is not a dependency, so run it alongside
uv run --with uvicorn uvicorn job_status.asgi:application --reload
```

## Drive it

```bash
# initialize → session id
SID=$(curl -s -i -X POST http://localhost:8000/mcp/ \
  -H 'Content-Type: application/json' \
  -H 'Mcp-Protocol-Version: 2025-11-25' \
  -H 'Origin: http://localhost' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2025-11-25","capabilities":{},
                 "clientInfo":{"name":"curl","version":"0"}}}' \
  | grep -i "^mcp-session-id:" | awk '{print $2}' | tr -d '\r')

# An array, so each header stays one argument once the shell expands it
H=(-H 'Content-Type: application/json'
   -H 'Mcp-Protocol-Version: 2025-11-25'
   -H 'Origin: http://localhost'
   -H "Mcp-Session-Id: $SID")

# Start a job that takes 3 seconds
JOB_ID=$(curl -s -X POST http://localhost:8000/mcp/ "${H[@]}" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call",
       "params":{"name":"jobs.start","arguments":{"duration_seconds":3}}}' \
  | python -c "import sys, json; print(json.load(sys.stdin)['result']['structuredContent']['id'])")

# Subscribe to push notifications (run in a separate terminal)
curl -N http://localhost:8000/mcp/ \
  -H 'Mcp-Protocol-Version: 2025-11-25' \
  -H 'Origin: http://localhost' \
  -H "Mcp-Session-Id: $SID"

# Poll the resource
curl -s -X POST http://localhost:8000/mcp/ "${H[@]}" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"resources/read\",
       \"params\":{\"uri\":\"jobs://$JOB_ID\"}}"
```

The SSE stream emits a `notifications/jobs/done` frame the moment the
worker thread finishes — clients that don't want to poll just listen.

## Where the patterns are documented

- Templated resources — [`docs/concepts.md`](../../docs/concepts.md)
- SSE push — [`docs/async.md`](../../docs/async.md) and
  [`docs/recipes/redis-sse-broker.md`](../../docs/recipes/redis-sse-broker.md)
  for cross-process fan-out.
- Resume after disconnect — [`docs/recipes/sse-replay-buffer.md`](../../docs/recipes/sse-replay-buffer.md).
