import re
from typing import Dict, Any, Optional
from govcareeros.backend.network.models import NetworkRequest, ResourceType, DataClassification

class RequestRecorder:
    SENSITIVE_KEYWORDS = {
        "authorization", "cookie", "set-cookie", "token", "api_key", 
        "password", "secret", "sessionid", "csrf"
    }

    def __init__(self):
        self.requests: Dict[str, NetworkRequest] = {}

    def sanitize_headers(self, headers: Dict[str, str]) -> Dict[str, str]:
        return {
            k: "[REDACTED]" if k.lower() in self.SENSITIVE_KEYWORDS else v 
            for k, v in headers.items()
        }

    def record(self, request_id: str, url: str, method: str, 
               resource_type: str, headers: Dict[str, str], 
               post_data: Optional[str] = None, initiator: Optional[str] = None) -> NetworkRequest:
        
        # Map playwright resource type to our internal enum
        res_type = ResourceType.OTHER
        if "xhr" in resource_type.lower(): res_type = ResourceType.XHR
        elif "fetch" in resource_type.lower(): res_type = ResourceType.FETCH
        elif "document" in resource_type.lower(): res_type = ResourceType.DOCUMENT
        elif "script" in resource_type.lower(): res_type = ResourceType.SCRIPT
        elif "websocket" in resource_type.lower(): res_type = ResourceType.WEBSOCKET
        
        req = NetworkRequest(
            request_id=request_id,
            url=url,
            method=method,
            resource_type=res_type,
            headers=self.sanitize_headers(headers),
            post_data=post_data, # Should be sanitized if it contains passwords
            initiator=initiator
        )
        self.requests[request_id] = req
        return req
