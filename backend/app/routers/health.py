"""Health + example endpoints.

`/api/health` is the one round-trip the frontend renders on load to prove the
FE <-> BE plumbing works. `/api/echo` is a tiny example POST you can copy as a
template for real endpoints.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import get_settings

router = APIRouter(prefix="/api", tags=["core"])


class HealthResponse(BaseModel):
    status: str
    app_name: str
    environment: str


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        app_name=settings.app_name,
        environment=settings.environment,
    )


class EchoRequest(BaseModel):
    message: str


class EchoResponse(BaseModel):
    echo: str


@router.post("/echo", response_model=EchoResponse)
def echo(req: EchoRequest) -> EchoResponse:
    return EchoResponse(echo=req.message)
