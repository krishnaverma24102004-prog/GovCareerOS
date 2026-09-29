import hashlib
from typing import Dict, Any, List

class EndpointChangeDetector:
    """
    Detects changes between two endpoint fingerprints.
    """
    def generate_fingerprint(self, candidate: Dict[str, Any]) -> str:
        # Use basic string concatenation to avoid PowerShell f-string interpolation issues during creation
        method = str(candidate.get("method", ""))
        url = str(candidate.get("url", ""))
        ct = str(candidate.get("content_type", ""))
        fingerprint_str = method + "|" + url + "|" + ct
        return hashlib.sha256(fingerprint_str.encode()).hexdigest()

    def compare(self, old_fp: str, new_fp: str) -> bool:
        return old_fp != new_fp
