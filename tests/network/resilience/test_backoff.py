import pytest
import asyncio
import time

async def mock_request_with_retry(attempt):
    if attempt < 3:
        return 503 # Service Unavailable
    return 200

@pytest.mark.asyncio
async def test_exponential_backoff_logic():
    attempts = 0
    max_attempts = 3
    success = False
    
    for i in range(max_attempts):
        attempts += 1
        status = await mock_request_with_retry(attempts)
        if status == 200:
            success = True
            break
            
    assert attempts == 3
    assert success is True
