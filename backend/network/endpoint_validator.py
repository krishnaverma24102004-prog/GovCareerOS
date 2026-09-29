import httpx
from typing import Dict, Any, Optional
from govcareeros.backend.network.models import EndpointCandidate

class EndpointValidator:
    """
    Validates whether a discovered endpoint can be replayed directly.
    """
    async def validate(self, candidate: EndpointCandidate, headers: Dict[str, str]) -> Dict[str, Any]:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.request(
                    method=candidate.method,
                    url=candidate.url,
                    headers=headers,
                    timeout=10.0
                )
                
                return {
                    "status": "SUCCESS",
                    "http_status": response.status_code,
                    "content_type": response.headers.get("content-type"),
                    "body_preview": response.text[:500],
                    "adapter_mode": "DIRECT_HTTP" if response.status_code == 200 else "BROWSER"
                }
            except Exception as e:
                return {
                    "status": "FAILED",
                    "error": str(e),
                    "adapter_mode": "BROWSER"
                }

