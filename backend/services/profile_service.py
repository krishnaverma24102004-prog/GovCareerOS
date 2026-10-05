import uuid
from datetime import date, datetime
from typing import Dict, Any, Optional
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from govcareeros.backend.repositories.profile_repository import ProfileRepository, SECTION_MODELS
from govcareeros.backend.repositories.fact_repository import FactRepository
from govcareeros.backend.models.user import ReservationCertificate

_VERSION_RETRIES = 10
# A held certificate is one claim: one Fact per certificate row, verified (or rejected) by a reviewer as a whole
CERTIFICATE_SUBJECT, CERTIFICATE_PREDICATE = "reservation_certificates", "certificate"

def certificate_fact_value(row: ReservationCertificate) -> str:
    """The claim a reviewer checks against the uploaded certificate; every recorded field, nothing else."""
    def d(v):
        return v.date().isoformat() if isinstance(v, datetime) else (v.isoformat() if v else "not stated")
    fmt = {True: "yes", False: "no", None: "not stated"}[row.central_format]
    return (f"{row.kind} certificate; issued {d(row.issue_date)}; income financial year {row.financial_year or 'not stated'}; "
            f"valid until {d(row.valid_until)}; issued by {row.issuing_authority or 'not stated'}; Central Government format: {fmt}")

class ProfileService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.profile_repo = ProfileRepository(session)
        self.fact_repo = FactRepository(session)

    async def create_initial_profile(self, user_id: uuid.UUID, data: Dict[str, Any]):
        version = await self.profile_repo.create_profile_version(user_id, 1, "Initial Profile")
        for edu in data.get("education", []):
            await self.profile_repo.add_education(version.id, **edu)
        for skill in data.get("skills", []):
            await self.profile_repo.add_skill(version.id, **skill)
        return version

    async def create_new_version(self, user_id: uuid.UUID, current_version_id: uuid.UUID, new_data: Dict[str, Any]):
        current = await self.profile_repo.get_profile_version(current_version_id)
        if current is None or current.user_id != user_id:
            raise ValueError("Profile version does not belong to this user")

        new_version_num = await self.profile_repo.next_version_number(user_id)
        new_version = await self.profile_repo.create_profile_version(user_id, new_version_num, "Updated Profile")
        # Full-row copy so the new version carries every field; the old version's rows are never touched
        await self.profile_repo.copy_version_entities(current_version_id, new_version.id)
        for edu in new_data.get("education", []):
            await self.profile_repo.add_education(new_version.id, **edu)
        for skill in new_data.get("skills", []):
            await self.profile_repo.add_skill(new_version.id, **skill)
        return new_version

    async def save_profile(self, user_id: uuid.UUID, sections: Dict[str, Any], change_summary: Optional[str], carry_over: bool):
        """
        Every write creates a new immutable ProfileVersion.
        carry_over=True (PATCH): sections not provided are copied from the latest version.
        carry_over=False (PUT): the payload is the whole profile.
        """
        await self.profile_repo.lock_user(user_id)
        for attempt in range(_VERSION_RETRIES):
            latest = await self.profile_repo.latest_version(user_id)
            number = await self.profile_repo.next_version_number(user_id)
            try:
                # Savepoint: a concurrent save that took this number fails only this insert, then we renumber
                async with self.session.begin_nested():
                    version = await self.profile_repo.create_profile_version(
                        user_id, number, change_summary or ("Initial Profile" if latest is None else "Updated Profile"))
                break
            except IntegrityError:
                if attempt == _VERSION_RETRIES - 1:
                    raise
        for key, model in SECTION_MODELS.items():
            payload = sections.get(key)
            if payload is None:
                if carry_over and latest is not None:
                    await self.profile_repo.copy_version_entities(latest.id, version.id, [model])
                continue
            for item in ([payload] if key == "personal_info" else payload):
                self.session.add(model(profile_version_id=version.id, **_datetimes(item)))
        await self.session.flush()
        await self.certificate_facts(version.id)
        return version

    async def certificate_facts(self, version_id: uuid.UUID) -> None:
        """Each certificate row of the version gets an UNVERIFIED Fact with USER_ATTESTED evidence naming the row
        (as resume claims do). Facts belong to one version: a reviewer verifies the certificate on that version."""
        rows = (await self.session.execute(select(ReservationCertificate).where(
            ReservationCertificate.profile_version_id == version_id))).scalars().all()
        for row in rows:
            fact = await self.fact_repo.create_fact(version_id, CERTIFICATE_SUBJECT, CERTIFICATE_PREDICATE, certificate_fact_value(row))
            await self.fact_repo.add_evidence(fact.id, "USER_ATTESTED", source=f"profile row {row.__tablename__}/{row.id}")

def _datetimes(item: Dict[str, Any]) -> Dict[str, Any]:
    # Profile date columns are DateTime; API accepts plain dates
    return {k: datetime(v.year, v.month, v.day) if isinstance(v, date) and not isinstance(v, datetime) else v
            for k, v in item.items()}
