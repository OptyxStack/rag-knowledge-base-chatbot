"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import admin, auth, conversations, dashboard, documents, health, reply, tickets
from app.core.config import get_settings
from app.core.gateway import GatewayMiddleware
from app.core.logging import setup_logging, get_logger
from app.core.metrics_middleware import MetricsMiddleware
from app.core.rate_limit import rate_limit_middleware
from app.core.tracing import setup_tracing

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown."""
    setup_logging(json_logs=True, log_level="INFO")
    logger.info("application_startup")
    # Load prompts, intents, and LLM config from DB into cache
    try:
        from app.db.session import async_session_factory
        from app.services.archi_config import refresh_cache as refresh_archi_config
        from app.services.branding_config import refresh_cache
        from app.services.doc_type_service import refresh_doc_type_cache
        from app.services.llm_config import refresh_cache as refresh_llm_config
        async with async_session_factory() as session:
            await refresh_cache(session)
            await refresh_doc_type_cache(session)
            await refresh_llm_config(session)
            await refresh_archi_config(session)
    except Exception as e:
        logger.warning("config_startup_failed", error=str(e))
    yield
    logger.info("application_shutdown")


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    settings = get_settings()
    # Docs: hide interactive UI in production; always serve openapi.json for API clients / in-app ApiReference
    docs_url = "/docs" if settings.docs_enabled else None
    redoc_url = "/redoc" if settings.docs_enabled else None
    openapi_url = "/openapi.json"  # Always serve schema (needed by ApiReference page)
    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        lifespan=lifespan,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
    )

    # CORS: * = allow all (dev); comma-separated origins for production
    origins_raw = (settings.cors_origins or "*").strip()
    allow_origins = ["*"] if origins_raw == "*" else [o.strip() for o in origins_raw.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(GatewayMiddleware)
    app.add_middleware(MetricsMiddleware)
    app.middleware("http")(rate_limit_middleware)

    # Mount routes
    prefix = settings.api_prefix
    app.include_router(health.router, prefix=prefix)
    app.include_router(auth.router, prefix=prefix)
    app.include_router(dashboard.router, prefix=prefix)
    app.include_router(conversations.router, prefix=prefix)
    app.include_router(reply.router, prefix=prefix)
    app.include_router(documents.router, prefix=prefix)
    app.include_router(tickets.router, prefix=prefix)
    app.include_router(admin.router, prefix=prefix)

    setup_tracing(app)

    return app


app = create_app()
