import re
import json
from typing import Dict, Any, Optional

class PaginationDetector:
    PAGE_PARAMS = {"page", "pageNumber", "pageIndex", "p"}
    OFFSET_PARAMS = {"offset", "start"}
    LIMIT_PARAMS = {"limit", "size", "count"}

    def detect(self, url: str, body: str) -> Optional[Dict[str, Any]]:
        # 1. Check URL query parameters
        if "?" in url:
            query_string = url.split("?", 1)[1]
            params = dict(re.findall(r"([^&=]+)=([^&]*)", query_string))
            
            for p in self.PAGE_PARAMS:
                if p in params:
                    return {"type": "page", "parameter": p, "initial": 1, "increment": 1}
            
            for p in self.OFFSET_PARAMS:
                if p in params:
                    limit_p = next((lp for lp in self.LIMIT_PARAMS if lp in params), "limit")
                    return {"type": "offset", "parameter": p, "limit_parameter": limit_p}

        # 2. Check JSON body for common pagination structures
        try:
            data = json.loads(body)
            if isinstance(data, dict):
                # Check for "pagination" key
                pagination = data.get("pagination")
                if isinstance(pagination, dict):
                    if "page" in pagination:
                        return {"type": "page", "parameter": "page", "initial": 1, "increment": 1}
                    if "cursor" in pagination:
                        return {"type": "cursor", "parameter": "cursor"}
                
                # Check for top-level pagination keys
                if "next_url" in data or "nextPage" in data:
                    return {"type": "cursor", "parameter": "next_url"}
        except (json.JSONDecodeError, TypeError):
            pass
            
        return None
