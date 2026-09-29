from typing import List, Dict, Any
from govcareeros.backend.network.network_discovery_engine import NetworkDiscoveryEngine

class EndpointDetector:
    def __init__(self, engine: NetworkDiscoveryEngine):
        self.engine = engine

    async def find_endpoints(self, url: str) -> List[Dict[str, Any]]:
        candidates = await self.engine.discover(url)
        return [c.__dict__ for c in candidates]
