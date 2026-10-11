from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from pathlib import Path

from app.core.config import settings
from app.api.v1.analyze import router as api_router

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Jev v8 Pure Decision-Support Terminal."
)

# Content Security Policy & Security Headers Middleware
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "script-src 'self'; "
            "connect-src 'self' https://api.github.com https://api.kraken.com;"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

app.add_middleware(SecurityHeadersMiddleware)

# CORS Configuration - Localhost only
allowed_origins_raw = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins_raw,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Mount Static Files
# Single source of truth: the repository-root index.html (also served by GitHub Pages)
# is served directly, eliminating the drift-prone duplicate under app/templates.
static_dir = BASE_DIR / "static"
data_dir = BASE_DIR.parent / "data"
INDEX_HTML_PATH = BASE_DIR.parent / "index.html"

static_dir.mkdir(parents=True, exist_ok=True)
data_dir.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
app.mount("/app/static", StaticFiles(directory=str(static_dir)), name="app_static")
app.mount("/data", StaticFiles(directory=str(data_dir)), name="data")

# Include API Router
app.include_router(api_router, prefix="/api")


@app.get("/", summary="Main Terminal UI")
async def root(request: Request):
    return FileResponse(str(INDEX_HTML_PATH), media_type="text/html")


@app.get("/health", summary="Health check")
async def health():
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "host": settings.HOST
    }
