from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

class SourceAdapter(ABC):
    def __init__(self, source_id: str, config: Optional[Dict[str, Any]] = None):
        self.source_id = source_id
        self.config = config or {}

    @abstractmethod
    async def discover(self) -> Dict[str, Any]:
        """Discover endpoints and analyze network traffic."""
        pass

    @abstractmethod
    async def health_check(self) -> Dict[str, Any]:
        """Verify source availability and adapter integrity."""
        pass

    @abstractmethod
    async def fetch(self) -> List[Dict[str, Any]]:
        """Retrieve raw data from the source."""
        pass

    @abstractmethod
    async def parse(self, raw_data: Any) -> List[Dict[str, Any]]:
        """Parse raw data into a structured intermediate format."""
        pass

    @abstractmethod
    async def normalize(self, parsed_data: Any) -> List[Dict[str, Any]]:
        """Normalize intermediate data into the canonical Vacancy model."""
        pass

    @abstractmethod
    async def get_documents(self, vacancy_id: str) -> List[str]:
        """Retrieve official documents associated with a vacancy."""
        pass

    @abstractmethod
    async def detect_changes(self) -> List[Dict[str, Any]]:
        """Detect corrigenda or updates to existing vacancies."""
        pass
