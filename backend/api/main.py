"""
GovCareerOS API.

    $env:PYTHONPATH="C:\\Users\\krishna"
    uvicorn govcareeros.backend.api.main:app --port 8080
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from govcareeros.backend.api.routers import admin, alerts, auth, digest, eligibility, exams, prep, profile, relevance, review, vacancies

def create_app() -> FastAPI:
    app = FastAPI(title="GovCareerOS", version="0.1.0")
    for router in (auth.router, profile.router, vacancies.router, eligibility.router, relevance.router, alerts.router, review.router, admin.router, digest.router, exams.router, prep.router):
from govcareeros.backend.config.settings import settings
    # Bearer tokens, no cookies: one explicit origin, never "*"
    app.add_middleware(CORSMiddleware, allow_origins=[settings.FRONTEND_ORIGIN], allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"])
        app.include_router(router)

    @app.get("/health", tags=["meta"])
    async def health():
        return {"status": "ok"}

    return app

app = create_app()
