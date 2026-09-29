import json
import re
from typing import Dict, Any, Optional, List

class EmbeddedStateDetector:
    """
    Extracts application state embedded in HTML <script> tags.
    """
    STATE_MARKERS = {
        "__NEXT_DATA__": "nextjs",
        "window.__INITIAL_STATE__": "generic",
        "window.__PRELOADED_STATE__": "generic",
        "__APOLLO_STATE__": "apollo"
    }

    def extract(self, html: str) -> List[Dict[str, Any]]:
        results = []
        for marker, source in self.STATE_MARKERS.items():
            # Look for the marker and the following JSON object
            pattern = rf"{re.escape(marker)}\s*=\s*({{.*?}});"
            # This is a simplified regex; a real one would handle nested braces
            matches = re.findall(pattern, html, re.DOTALL)
            for match in matches:
                try:
                    results.append({
                        "source": source,
                        "marker": marker,
                        "data": json.loads(match)
                    })
                except json.JSONDecodeError:
                    continue
        return results
