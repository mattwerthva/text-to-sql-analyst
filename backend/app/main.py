"""FastAPI application entrypoint.

Run in dev with:  uv run fastapi dev app/main.py
Interactive docs:  http://localhost:8000/docs
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import ask, health

settings = get_settings()

app = FastAPI(title=settings.app_name)

# CORS is a no-op in dev (the Vite proxy serves same-origin), but is here so the
# backend also works when called directly or deployed separately from the SPA.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(ask.router)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": settings.app_name, "docs": "/docs"}
