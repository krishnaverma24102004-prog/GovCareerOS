"""
GovCareerOS API.

    $env:PYTHONPATH="C:\\Users\\krishna"
    uvicorn govcareeros.backend.api.main:app --port 8080
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from govcareeros.backend.config.settings import settings
from govcareeros.backend.api.routers import (
    admin, alerts, applications, auth, digest, eligibility, exam_versions, exams, gaps, prep, profile, relevance, resume, review, vacancies,
)
from govcareeros.backend.api.routers import admin_console, review_console

def create_app() -> FastAPI:
    app = FastAPI(title="GovCareerOS", version="0.1.0")
    # Bearer tokens, no cookies: one explicit origin, never "*"
    app.add_middleware(CORSMiddleware, allow_origins=[settings.FRONTEND_ORIGIN], allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"])
    for router in (auth.router, profile.router, vacancies.router, eligibility.router, relevance.router, alerts.router,
                   review.router, admin.router, digest.router, exams.router, prep.router, gaps.router, applications.router,
                   resume.router, review_console.router, admin_console.router):
                   resume.router, exam_versions.router):
        app.include_router(router)

    @app.get("/health", tags=["meta"])
    async def health():
        return {"status": "ok"}

    return app

app = create_app()
