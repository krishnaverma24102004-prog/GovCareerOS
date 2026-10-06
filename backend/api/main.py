"""
GovCareerOS API.

    $env:PYTHONPATH="C:\\Users\\krishna"
    uvicorn govcareeros.backend.api.main:app --port 8080
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from govcareeros.backend.config.settings import settings
from govcareeros.backend.config.log_setup import configure_logging
from govcareeros.backend.api.body_limit import BodyLimitMiddleware
from govcareeros.backend.api.secure_headers import SecureHeadersMiddleware
from govcareeros.backend.services import notice_verifier
from govcareeros.backend.api.routers import (
    admin, admin_console, alerts, applications, auth, digest, eligibility, exam_versions, exams, gaps, prep, profile,
    relevance, resume, review, review_console, vacancies, verify,
)

def create_app() -> FastAPI:
    # LOG_FORMAT=json (production): runs after uvicorn set up its loggers, so they are routed to the JSON handler too
    configure_logging(settings.LOG_FORMAT, settings.LOG_LEVEL)
    app = FastAPI(title="GovCareerOS", version="0.1.0")
    # The public notice check takes anonymous uploads: refuse oversized bodies before they are parsed or spooled;
    # every other route gets the general default. Added before CORS so CORS stays outermost and the browser can read the 413.
    app.add_middleware(BodyLimitMiddleware, limits={"/verify": lambda: notice_verifier.MAX_REQUEST_BYTES},
                       default=lambda: settings.MAX_REQUEST_BYTES)
    # Bearer tokens, no cookies: explicit origins only (FRONTEND_ORIGIN + FRONTEND_ORIGINS), never "*"
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"])
    # Outermost, so the CORS preflight and the 413 carry the headers too
    app.add_middleware(SecureHeadersMiddleware, hsts_seconds=lambda: settings.HSTS_SECONDS)
    for router in (auth.router, profile.router, vacancies.router, eligibility.router, relevance.router, alerts.router,
                   review.router, admin.router, digest.router, exams.router, prep.router, gaps.router, applications.router,
                   resume.router, review_console.router, admin_console.router, exam_versions.router, verify.router):
        app.include_router(router)

    @app.get("/health", tags=["meta"])
    async def health():
        return {"status": "ok"}

    return app

app = create_app()
