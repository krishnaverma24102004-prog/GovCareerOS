from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Optional, List
from enum import Enum

class ResourceType(Enum):
    XHR = "xhr"
    FETCH = "fetch"
    DOCUMENT = "document"
    SCRIPT = "script"
    JSON = "json"
    PDF = "pdf"
    IMAGE = "image"
    MEDIA = "media"
    EVENTSOURCE = "eventsource"
    WEBSOCKET = "websocket"
    OTHER = "other"

class DataClassification(Enum):
    PUBLIC_DATA = "PUBLIC_DATA"
    AUTHENTICATED_DATA = "AUTHENTICATED_DATA"
    SENSITIVE = "SENSITIVE"
    UNKNOWN = "UNKNOWN"

@dataclass
class NetworkRequest:
    request_id: str
    url: str
    method: str
    resource_type: ResourceType
    headers: Dict[str, str]
    post_data: Optional[str] = None
    query_params: Dict[str, str] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    initiator: Optional[str] = None

@dataclass
class NetworkResponse:
    request_id: str
    status: int
    headers: Dict[str, str]
    body: Optional[str] = None
    content_type: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    timing: Dict[str, float] = field(default_factory=dict)
    redirects: List[str] = field(default_factory=list)
    classification: DataClassification = DataClassification.UNKNOWN

@dataclass
class EndpointCandidate:
    url: str
    method: str
    resource_type: ResourceType
    content_type: Optional[str]
    status: int
    is_candidate_api: bool
    confidence: float
    reasons: List[str] = field(default_factory=list)
    detected_fields: List[str] = field(default_factory=list)
    pagination: Optional[Dict[str, Any]] = None

