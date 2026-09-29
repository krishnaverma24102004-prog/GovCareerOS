import asyncio
import pytest
import subprocess
import time
import httpx
import os
import sys
from govcareeros.backend.network.network_discovery_engine import NetworkDiscoveryEngine
from govcareeros.backend.network.adapter_generator import AdapterGenerator
from govcareeros.backend.network.endpoint_validator import EndpointValidator
from govcareeros.backend.network.models import ResourceType

@pytest.mark.asyncio
async def test_full_network_pipeline():
    # 1. Start Local Server in background
    server_proc = subprocess.Popen(
        [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "mock_portal", "server.py")],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    time.sleep(2) # Give server time to start

    try:
        # 2. Run Discovery Engine
        engine = NetworkDiscoveryEngine()
        candidates = await engine.discover("http://127.0.0.1:8000/")
        
        assert len(candidates) > 0, "No candidate endpoints discovered"
        
        # Select the best candidate
        best = candidates[0]
        
        print(f"\n--- Observed Endpoint ---\nURL: {best.url}\nMethod: {best.method}\nConfidence: {best.confidence}\n")
        
        assert "api/recruitments" in best.url
        assert best.is_candidate_api is True
        assert best.pagination["type"] == "page"
        
        # 3. Validate the Endpoint (Direct Replay)
        validator = EndpointValidator()
        validation_res = await validator.validate(best, headers={})
        
        assert validation_res["status"] == "SUCCESS"
        assert validation_res["adapter_mode"] == "DIRECT_HTTP"
        
        # 4. Generate Adapter
        generator = AdapterGenerator()
        adapter = generator.generate(best, adapter_mode=validation_res["adapter_mode"])
        
        assert adapter["endpoint"] == best.url
        assert adapter["adapter_mode"] == "DIRECT_HTTP"
        
        print(f"Generated Adapter: {adapter}")

    finally:
        server_proc.terminate()

if __name__ == "__main__":
    asyncio.run(test_full_network_pipeline())
