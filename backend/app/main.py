from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.auth import router as auth_router
from app.api.conversions import router as conversion_router
from app.core.config import config
from app.core.database import Base, engine
from app.providers.base import ProviderError


@asynccontextmanager
async def lifespan(app):
    if config.dev_env != "dev":
        if (
            config.secret_key == "local-development-only-change-before-deploying"
            or len(config.secret_key) < 32
            or not config.token_encryption_key
            or not config.cookie_secure
            or config.demo_mode
        ):
            raise RuntimeError(
                "Production requires strong secrets, encryption key, secure cookies, and DEMO_MODE=false"
            )
    if config.database_url.startswith("sqlite") and config.dev_env == "dev":
        Base.metadata.create_all(engine)
    yield


app = FastAPI(title=config.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[config.frontend_url.rstrip("/")],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-Requested-With"],
)


@app.middleware("http")
async def csrf(request: Request, call_next):
    if request.method in ("POST", "PATCH", "PUT", "DELETE"):
        if request.headers.get("X-Requested-With") != "PlaylistConverter":
            return JSONResponse({"detail": "Missing CSRF request header"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != config.frontend_url.rstrip("/"):
            return JSONResponse({"detail": "Untrusted origin"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(ProviderError)
async def provider_error(request, error):
    return JSONResponse({"detail": str(error)}, status_code=503 if error.retryable else 400)


app.include_router(auth_router)
app.include_router(conversion_router)


@app.get("/")
def root():
    return {"message": "Welcome to Converter API!"}


@app.get("/health")
def health():
    from sqlalchemy import text

    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ok", "environment": config.dev_env}
