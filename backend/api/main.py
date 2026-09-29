"""
GovCareerOS API.

    $env:PYTHONPATH="C:\\Users\\krishna"
    uvicorn govcareeros.backend.api.main:app --port 8080
"""
from fastapi import FastAPI

from govcareeros.backend.api.routers import alerts, auth, eligibility, profile, relevance, vacancies

def create_app() -> FastAPI:
    app = FastAPI(title="GovCareerOS", version="0.1.0")
    for router in (auth.router, profile.router, vacancies.router, eligibility.router, relevance.router, alerts.router):
        app.include_router(router)

    @app.get("/health", tags=["meta"])
    async def health():
        return {"status": "ok"}

    return app

app = create_app()
