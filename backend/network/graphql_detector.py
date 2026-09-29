import json
from typing import Dict, Any, Optional, Tuple
from govcareeros.backend.network.models import EndpointCandidate

class GraphQLDetector:
    """
    Detects GraphQL endpoints by inspecting request bodies and response structures.
    """
    def detect(self, url: str, method: str, body: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        if method.upper() != "POST":
            return False, None
        
        if "/graphql" in url.lower():
            return True, {"type": "url_match"}
            
        try:
            data = json.loads(body)
            if "query" in data or "operationName" in data:
                return True, {
                    "operationName": data.get("operationName"),
                    "query": data.get("query"),
                    "variables": data.get("variables")
                }
        except (json.JSONDecodeError, TypeError):
            pass
            
        return False, None
