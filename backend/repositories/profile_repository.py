import uuid
from typing import Any, Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func, update
from govcareeros.backend.models.user import User, ProfileVersion, Education, Experience, Skill, Project, Certification, Language, Achievement, PersonalInfo, UserDocument, ReservationCertificate
from govcareeros.backend.models.fact import Fact, Evidence

# Child entities that belong to a single ProfileVersion snapshot
VERSIONED_ENTITIES = [PersonalInfo, Education, Experience, Project, Skill, Certification, Language, Achievement, UserDocument,
                      ReservationCertificate]
_NON_COPIED_COLUMNS = {"id", "profile_version_id", "created_at", "updated_at"}
# API section name -> model
SECTION_MODELS = {
    "personal_info": PersonalInfo, "education": Education, "experience": Experience, "projects": Project,
    "skills": Skill, "certifications": Certification, "languages": Language, "achievements": Achievement,
    "documents": UserDocument, "reservation_certificates": ReservationCertificate,
}

class ProfileRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_user(self, email: str, hashed_password: str, full_name: Optional[str] = None) -> User:
        user = User(email=email, hashed_password=hashed_password, full_name=full_name)
        self.session.add(user)
        await self.session.flush()
        return user

    async def create_profile_version(self, user_id: uuid.UUID, version_number: int, change_summary: Optional[str] = None) -> ProfileVersion:
        version = ProfileVersion(user_id=user_id, version_number=version_number, change_summary=change_summary)
        self.session.add(version)
        await self.session.flush()
        return version

    async def get_profile_version(self, version_id: uuid.UUID) -> Optional[ProfileVersion]:
        return await self.session.get(ProfileVersion, version_id)

    async def lock_user(self, user_id: uuid.UUID) -> None:
        """Serialise a user's profile saves until commit (row lock on PostgreSQL, write lock on SQLite), so a save
        reads the latest *committed* version to carry over; SELECT ... FOR UPDATE would be a no-op on SQLite."""
        await self.session.execute(update(User).where(User.id == user_id).values(updated_at=func.now()))

    async def next_version_number(self, user_id: uuid.UUID) -> int:
        res = await self.session.execute(
            select(func.max(ProfileVersion.version_number)).where(ProfileVersion.user_id == user_id)
        )
        return (res.scalar() or 0) + 1

    async def copy_version_entities(self, from_version_id: uuid.UUID, to_version_id: uuid.UUID, models=VERSIONED_ENTITIES) -> None:
        for model in models:
            res = await self.session.execute(select(model).where(model.profile_version_id == from_version_id))
            for row in res.scalars().all():
                values = {
                    c.key: getattr(row, c.key)
                    for c in model.__table__.columns
                    if c.key not in _NON_COPIED_COLUMNS
                }
                self.session.add(model(profile_version_id=to_version_id, **values))
        await self.session.flush()

    async def latest_version(self, user_id: uuid.UUID) -> Optional[ProfileVersion]:
        res = await self.session.execute(
            select(ProfileVersion).where(ProfileVersion.user_id == user_id)
            .order_by(ProfileVersion.version_number.desc(), ProfileVersion.created_at.desc()).limit(1)
        )
        return res.scalar_one_or_none()

    async def list_versions(self, user_id: uuid.UUID) -> List[ProfileVersion]:
        res = await self.session.execute(
            select(ProfileVersion).where(ProfileVersion.user_id == user_id).order_by(ProfileVersion.version_number)
        )
        return list(res.scalars().all())

    async def get_owned_version(self, user_id: uuid.UUID, version_id: uuid.UUID) -> Optional[ProfileVersion]:
        version = await self.session.get(ProfileVersion, version_id)
        return version if version is not None and version.user_id == user_id else None

    async def load_sections(self, version_id: uuid.UUID) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key, model in SECTION_MODELS.items():
            res = await self.session.execute(select(model).where(model.profile_version_id == version_id).order_by(model.created_at, model.id))
            rows = list(res.scalars().all())
            out[key] = (rows[0] if rows else None) if key == "personal_info" else rows
        return out

    async def add_education(self, version_id: uuid.UUID, degree: str, institution: str, **kwargs) -> Education:
        edu = Education(profile_version_id=version_id, degree=degree, institution=institution, **kwargs)
        self.session.add(edu)
        await self.session.flush()
        return edu

    async def add_skill(self, version_id: uuid.UUID, skill_name: str, skill_type: str, proficiency: str = "Intermediate") -> Skill:
        skill = Skill(profile_version_id=version_id, skill_name=skill_name, skill_type=skill_type, proficiency=proficiency)
        self.session.add(skill)
        await self.session.flush()
        return skill

    async def get_profile_snapshot(self, version_id: uuid.UUID) -> Dict[str, Any]:
        # Logic to aggregate all child entities for a specific version
        result = {"version_id": version_id, "education": [], "skills": []}
        
        res = await self.session.execute(select(Education).where(Education.profile_version_id == version_id))
        result["education"] = [e.degree for e in res.scalars().all()]
        
        res = await self.session.execute(select(Skill).where(Skill.profile_version_id == version_id))
        result["skills"] = [s.skill_name for s in res.scalars().all()]
        
        return result
