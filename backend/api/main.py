"""
GovCareerOS API.

    $env:PYTHONPATH="C:\\Users\\krishna"
    uvicorn govcareeros.backend.api.main:app --port 8080
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from govcareeros.backend.config.settings import settings
from govcareeros.backend.api.body_limit import BodyLimitMiddleware
from govcareeros.backend.services import notice_verifier
from govcareeros.backend.api.routers import (
    admin, admin_console, alerts, applications, auth, deputation, digest, eligibility, exam_versions, exams, gaps, prep, profile,
    relevance, resume, review, review_console, vacancies, verify,
)

def create_app() -> FastAPI:
    app = FastAPI(title="GovCareerOS", version="0.1.0")
    # The public notice check takes anonymous uploads: refuse oversized bodies before they are parsed or spooled.
    # Added before CORS so CORS stays outermost and the browser can read the 413.
    app.add_middleware(BodyLimitMiddleware, limits={"/verify": lambda: notice_verifier.MAX_REQUEST_BYTES})
    # Bearer tokens, no cookies: one explicit origin, never "*"
    app.add_middleware(CORSMiddleware, allow_origins=[settings.FRONTEND_ORIGIN], allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"])
    for router in (auth.router, profile.router, vacancies.router, eligibility.router, relevance.router, alerts.router,
                   review.router, admin.router, digest.router, exams.router, prep.router, gaps.router, applications.router,
                   resume.router, review_console.router, admin_console.router, exam_versions.router, verify.router, deputation.router):
        app.include_router(router)

    @app.get("/health", tags=["meta"])
    async def health():
        return {"status": "ok"}

    return app

app = create_app()
