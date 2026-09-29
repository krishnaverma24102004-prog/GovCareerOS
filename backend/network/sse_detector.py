import re
from typing import Dict, Any, Optional

class SSEDetector:
    """
    Detects Server-Sent Events (SSE) based on content-type and frame patterns.
    """
    def is_sse(self, content_type: str) -> bool:
        return "text/event-stream" in content_type.lower()

    def parse_frame(self, frame: str) -> Dict[str, Any]:
        data = {}
        for line in frame.split("\n"):
            if line.startswith("data:"):
                data["data"] = line[5:].strip()
            elif line.startswith("event:"):
                data["event"] = line[6:].strip()
            elif line.startswith("id:"):
                data["id"] = line[3:].strip()
        return data
