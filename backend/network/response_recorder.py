from typing import Dict, Any, Optional
from govcareeros.backend.network.models import NetworkResponse, DataClassification

class ResponseRecorder:
    def __init__(self):
        self.responses: Dict[str, NetworkResponse] = {}

    def classify_data(self, url: str, headers: Dict[str, str], body: Optional[str]) -> DataClassification:
        # Simple heuristic for classification
        url_lower = url.lower()
        if any(kw in url_lower for kw in ["auth", "login", "session", "token"]):
            return DataClassification.SENSITIVE
        
        # Check headers for auth
        if "authorization" in [k.lower() for k in headers.keys()]:
            return DataClassification.AUTHENTICATED_DATA
            
        return DataClassification.PUBLIC_DATA

    def record(self, request_id: str, status: int, headers: Dict[str, str], 
               body: Optional[str] = None, content_type: Optional[str] = None, 
               timing: Optional[Dict[str, float]] = None) -> NetworkResponse:
        
        res = NetworkResponse(
            request_id=request_id,
            status=status,
            headers=headers,
            body=body,
            content_type=content_type,
            timing=timing or {},
            classification=self.classify_data(request_id, headers, body) # Using request_id as proxy for URL here, should be proper URL
        )
        self.responses[request_id] = res
        return res
