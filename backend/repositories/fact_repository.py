import uuid
from typing import Any, Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from govcareeros.backend.models.fact import Fact, Evidence
from govcareeros.backend.models.artifact import RawDocument

class FactRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_fact(self, version_id: uuid.UUID, subject: str, predicate: str, value: str, status: str = "UNVERIFIED") -> Fact:
        fact = Fact(profile_version_id=version_id, subject=subject, predicate=predicate, value=value, status=status)
        self.session.add(fact)
        await self.session.flush()
        return fact

    async def add_evidence(self, fact_id: uuid.UUID, evidence_type: str, artifact_id: Optional[uuid.UUID] = None, source: Optional[str] = None) -> Evidence:
        evidence = Evidence(fact_id=fact_id, evidence_type=evidence_type, artifact_id=artifact_id, source=source)
        self.session.add(evidence)
        await self.session.flush()
        return evidence

    async def get_fact_chain(self, fact_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        res = await self.session.execute(select(Fact).where(Fact.id == fact_id))
        fact = res.scalar_one_or_none()
        if not fact: return None
        
        res = await self.session.execute(
            select(Evidence, RawDocument)
            .outerjoin(RawDocument, Evidence.artifact_id == RawDocument.id)
            .where(Evidence.fact_id == fact.id)
        )
        rows = res.all()

        return {
            "fact": fact.value,
            "status": fact.status,
            "evidence": [e.source for e, _ in rows],
            "artifacts": [
                {"raw_document_id": doc.id, "content_hash": doc.content_hash, "url": doc.url}
                for _, doc in rows if doc is not None
            ],
        }
