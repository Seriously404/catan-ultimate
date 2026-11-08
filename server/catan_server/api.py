"""Route registration. Add future HTTP and WebSocket feature routers here."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/health")
async def health() -> dict[str, str]:
    """Report process availability; this does not imply a game is running."""
    return {"status": "ok"}
