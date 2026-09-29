import asyncio
import json
from datetime import datetime
from govcareeros.backend.network.browser_session import BrowserSession
from govcareeros.backend.network.auth_detector import AuthDetector
from govcareeros.backend.network.network_discovery_engine import NetworkDiscoveryEngine
from govcareeros.backend.network.models import ResourceType

async def real_upsc_verification():
    source_id = "upsc"
    source_url = "https://upsc.gov.in/jobs-notifications"
    
    print(f"--- STARTING AUTHENTICATED VERIFICATION FOR {source_id.upper()} ---")
    
    # 1. Reuse Existing Profile
    # The BrowserSession class uses settings.STORAGE_PATH + "/browser_profiles/" + source_id
    session = BrowserSession(source_id=source_id)
    
    async with session:
        page = await session.new_page()
        
        # 2. Verify Authentication
        detector = AuthDetector(source_id)
        await page.goto(source_url)
        auth_state = await detector.check_auth_state(page)
        
        print(f"Source: {source_id}")
        print(f"Profile Path: {session.user_data_dir}")
        print(f"Session State: {auth_state}")
        
        if auth_state != "AUTHENTICATED":
            print("CRITICAL: Session is not authenticated. User must login again.")
            return
            
        print("Authentication Verified. Proceeding to discovery...")
        
        # 3. Network Observation
        # We use the DiscoveryEngine which wraps the recording logic
        engine = NetworkDiscoveryEngine()
        # Note: The engine creates its own session internally. 
        # To use the SAME authenticated session, we need to inject it.
        # Since the current Engine class creates its own session, 
        # we will manually use the recorder/classifier on the existing page.
        
        from govcareeros.backend.network.request_recorder import RequestRecorder
        from govcareeros.backend.network.response_recorder import ResponseRecorder
        from govcareeros.backend.network.api_classifier import APIClassifier
        
        req_rec = RequestRecorder()
        res_rec = ResponseRecorder()
        classifier = APIClassifier()
        
        async def handle_req(req):
            rid = f"{req.method}_{req.url}"
            req_rec.record(rid, req.url, req.method, req.resource_type, req.headers)
            
        async def handle_res(res):
            rid = f"{res.request.method}_{res.request.url}"
            if res.request.resource_type in ["xhr", "fetch"]:
                try:
                    body = await res.text()
                    res_rec.record(rid, res.status, dict(res.headers), body, res.headers.get("content-type"))
                except: pass
        
        page.on("request", handle_req)
        page.on("response", handle_res)
        
        # Reload to trigger network events with observers active
        await page.reload(wait_until="networkidle")
        
        # 4. Find Real Recruitment Notification
        candidates = []
        for rid, req in req_rec.requests.items():
            res = res_rec.responses.get(rid)
            if res:
                is_cand, conf, reasons, fields = classifier.classify(req.url, res.content_type or "", res.body or "")
                if is_cand:
                    candidates.append({"url": req.url, "conf": conf, "fields": fields})
        
        if not candidates:
            print("No structured API candidates found. Searching for documents...")
            # Fallback: Find PDF links
            links = await page.query_selector_all("a")
            for link in links:
                href = await link.get_attribute("href")
                text = await link.inner_text()
                if href and (".pdf" in href.lower() or "notification" in text.lower()):
                    print(f"FOUND DOCUMENT: {text} -> {href}")
                    # Here we would trigger the PDF parser
                    break
        else:
            print(f"Found {len(candidates)} structured candidates. Best: {candidates[0]['url']}")

if __name__ == "__main__":
    asyncio.run(real_upsc_verification())