import json
from typing import Dict, Any, Optional
from govcareeros.backend.network.models import EndpointCandidate

class AdapterGenerator:
    """
    Generates a structured adapter definition from an EndpointCandidate.
    """
    def generate(self, candidate: EndpointCandidate, adapter_mode: str = "BROWSER") -> Dict[str, Any]:
        return {
            "adapter_mode": adapter_mode,
            "discovery_method": candidate.resource_type.value,
            "endpoint": candidate.url,
            "method": candidate.method,
            "content_type": candidate.content_type,
            "pagination": candidate.pagination,
            "fields_detected": candidate.detected_fields,
            "confidence": candidate.confidence,
            "reasons": candidate.reasons
        }

