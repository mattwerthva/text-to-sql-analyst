# backend

FastAPI service for the text-to-SQL analyst. Managed with [uv](https://docs.astral.sh/uv/).

## Setup & run

```bash
cp .env.example .env
uv sync                        # create .venv + install deps (incl. dev group)
uv run fastapi dev app/main.py # http://localhost:8000  (docs at /docs)
```

## Test / lint

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
```

## Layout

```
app/
├── main.py           # FastAPI app + middleware, registers routers
├── config.py         # typed settings from env / .env
└── routers/
    └── health.py     # /api/health, /api/echo (copy as a template)
tests/
└── test_health.py
```

## Adding an endpoint

1. Create `app/routers/<name>.py` with an `APIRouter`.
2. `app.include_router(<name>.router)` in `app/main.py`.
3. Add typed request/response Pydantic models.
4. Add a matching typed function in `frontend/src/lib/api.ts`.
