from typing import Dict, Any, List, Tuple
import json

class SchemaDetector:
    """
    Analyzes JSON response bodies to detect data structures and field types.
    """
    def analyze(self, body: str) -> Dict[str, Any]:
        try:
            data = json.loads(body)
            return self._analyze_recursive(data)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON"}

    def _analyze_recursive(self, data: Any, path: str = "") -> Any:
        if isinstance(data, list):
            if not data:
                return {"type": "list", "items": "unknown"}
            # Analyze the first item as a representative of the list
            return {"type": "list", "items": self._analyze_recursive(data[0], path + "[]")}
        elif isinstance(data, dict):
            schema = {"type": "object", "properties": {}}
            for k, v in data.items():
                schema["properties"][k] = self._analyze_recursive(v, f"{path}.{k}")
            return schema
        else:
            return {"type": type(data).__name__}

