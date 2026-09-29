import asyncio
import uuid
from typing import List, Dict, Any, Optional
from govcareeros.backend.network.browser_session import BrowserSession
from govcareeros.backend.network.request_recorder import RequestRecorder
from govcareeros.backend.network.response_recorder import ResponseRecorder
from govcareeros.backend.network.api_classifier import APIClassifier
from govcareeros.backend.network.pagination_detector import PaginationDetector
from govcareeros.backend.network.models import EndpointCandidate, ResourceType

class NetworkDiscoveryEngine:
    def __init__(self):
        self.request_recorder = RequestRecorder()
        self.response_recorder = ResponseRecorder()
        self.classifier = APIClassifier()
        self.pagination = PaginationDetector()

    async def discover(self, url: str) -> List[EndpointCandidate]:
        candidates = []
        
        async with BrowserSession() as session:
            page = await session.new_page()
            request_id_map = {}
            
            async def wrap_request(req):
                rid = str(uuid.uuid4())
                request_id_map[req] = rid
                
                # FIX: Playwright post_data is not an awaitable in all versions/contexts
                # Use a safe check
                p_data = None
                try:
                    p_data = await req.post_data if asyncio.iscoroutine(req.post_data) else req.post_data
                except Exception:
                    pass
                
                self.request_recorder.record(
                    request_id=rid, url=req.url, method=req.method,
                    resource_type=req.resource_type, headers=req.headers,
                    post_data=p_data, initiator=None
                )

            async def wrap_response(res):
                rid = request_id_map.get(res.request)
                if rid and res.request.resource_type in ["xhr", "fetch"]:
                    try:
                        body = await res.text()
                        self.response_recorder.record(
                            request_id=rid, status=res.status,
                            headers=dict(res.headers), body=body,
                            content_type=res.headers.get("content-type")
                        )
                    except Exception: pass

            page.on("request", wrap_request)
            page.on("response", wrap_response)
            
            try:
                await page.goto(url, wait_until="networkidle", timeout=60000)
                await asyncio.sleep(5) 
            except Exception as e:
                print(f"Navigation error: {e}")

            for req_id, req in self.request_recorder.requests.items():
                res = self.response_recorder.responses.get(req_id)
                if not res: continue
                
                if req.resource_type in [ResourceType.XHR, ResourceType.FETCH]:
                    is_candidate, conf, reasons, fields = self.classifier.classify(
                        req.url, res.content_type or "", res.body or ""
                    )
                    
                    if is_candidate:
                        pagination = self.pagination.detect(req.url, res.body or "")
                        candidates.append(EndpointCandidate(
                            url=req.url,
                            method=req.method,
                            resource_type=req.resource_type,
                            content_type=res.content_type,
                            status=res.status,
                            is_candidate_api=True,
                            confidence=conf,
                            reasons=reasons,
                            detected_fields=fields,
                            pagination=pagination
                        ))
        
        return candidates
