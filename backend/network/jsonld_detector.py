import json
import re
from typing import Dict, Any, Optional, List

class JSONLDDetector:
    def extract(self, html: str) -> List[Dict[str, Any]]:
        results = []
        # We use a string instead of a raw string to avoid PowerShell redirection issues during file creation
        pattern = "<script type=\"application/ld+json\">(.*?)</script>"
        # Actually we need to escape the regex
        regex_pattern = re.compile(r"<script type=\"application/ld\+json\">(.*?)</script>", re.DOTALL)
        matches = regex_pattern.findall(html)
        
        for match in matches:
            try:
                data = json.loads(match.strip())
                results.append(data)
            except json.JSONDecodeError:
                continue
        return results
