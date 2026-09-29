from typing import Dict, Any, Optional
from datetime import datetime

class WebSocketDetector:
    """
    Detects and records WebSocket activity.
    """
    def __init__(self):
        self.sessions = {}

    def record_connection(self, ws_id: str, url: str):
        self.sessions[ws_id] = {
            "url": url,
            "connected_at": datetime.now(),
            "frames": []
        }

    def record_frame(self, ws_id: str, direction: str, payload: str):
        if ws_id in self.sessions:
            self.sessions[ws_id]["frames"].append({
                "timestamp": datetime.now(),
                "direction": direction,
                "payload": payload
            })
