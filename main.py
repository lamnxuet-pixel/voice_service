"""FastAPI application — lifespan, middleware, and router registration."""

from __future__ import annotations

import pathlib
from contextlib import asynccontextmanager
from datetime import datetime

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import async_session_factory, engine
from app.models.patient import Base
from app.routers import patients, voice
from app.services import patient_service, session_service

# Configure structured logging
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer() if settings.debug else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(0),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup and shutdown events."""
    logger.info("application_starting", host=settings.host, port=settings.port)

    # Create tables if they don't exist (for dev; use Alembic in production)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("database_tables_ready")

    # Seed demo data if table is empty
    from app.seed import seed_patients
    async with async_session_factory() as session:
        await seed_patients(session)

    yield

    # Shutdown
    logger.info("application_shutting_down")
    
    await engine.dispose()


app = FastAPI(
    title="Voice Patient Registration Service",
    description="AI-powered patient intake via phone calls using Vapi + Gemini",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(patients.router)
app.include_router(voice.router)

# Serve static UI
_static_dir = pathlib.Path(__file__).parent / "app" / "static"
app.mount("/ui", StaticFiles(directory=str(_static_dir), html=True), name="static")


# --- Health Check ---

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0.0",
    }


@app.get("/")
async def root():
    """Redirect root to the dashboard UI."""
    return RedirectResponse(url="/ui/index.html")
