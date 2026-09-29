from enum import Enum
from typing import Any, Dict, Optional, List
from govcareeros.backend.sources.base import SourceAdapter

class DiscoveryMethod(Enum):
    OFFICIAL_API = "OFFICIAL_API"
    KNOWN_ENDPOINT = "KNOWN_ENDPOINT"
    SITEMAP = "SITEMAP"
    RSS = "RSS"
    ATOM = "ATOM"
    XHR = "XHR"
    FETCH = "FETCH"
    GRAPHQL = "GRAPHQL"
    WEBSOCKET = "WEBSOCKET"
    SSE = "SSE"
    EMBEDDED_STATE = "EMBEDDED_STATE"
    JSON_LD = "JSON_LD"
    HTML = "HTML"
    PDF = "PDF"
    DOCX = "DOCX"
    OCR = "OCR"
    MANUAL_REVIEW = "MANUAL_REVIEW"

class DiscoveryStrategySelector:
    """
    Determines the most efficient acquisition method based on source capability.
    Implements the fallback hierarchy.
    """
    def __init__(self, network_engine: Any):
        self.network_engine = network_engine

    async def select_strategy(self, source_id: str, source_config: Dict[str, Any]) -> DiscoveryMethod:
        # Hierarchical Fallback Order
        hierarchy = [
            DiscoveryMethod.OFFICIAL_API,
            DiscoveryMethod.KNOWN_ENDPOINT,
            DiscoveryMethod.SITEMAP,
            DiscoveryMethod.RSS,
            DiscoveryMethod.ATOM,
            DiscoveryMethod.XHR,
            DiscoveryMethod.FETCH,
            DiscoveryMethod.GRAPHQL,
            DiscoveryMethod.WEBSOCKET,
            DiscoveryMethod.SSE,
            DiscoveryMethod.EMBEDDED_STATE,
            DiscoveryMethod.JSON_LD,
            DiscoveryMethod.HTML,
            DiscoveryMethod.PDF,
            DiscoveryMethod.DOCX,
            DiscoveryMethod.OCR,
            DiscoveryMethod.MANUAL_REVIEW
        ]
        
        # In a real implementation, this would iterate through the hierarchy
        # checking if the method is enabled/available for the source.
        return DiscoveryMethod.HTML # Default fallback
