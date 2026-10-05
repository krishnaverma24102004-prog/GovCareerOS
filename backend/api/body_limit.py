"""
Request body limits per path, enforced before FastAPI parses (and spools to disk) a form or upload.

A Content-Length above the limit is refused at once, without reading the body; a body without one (chunked) is
counted as it arrives and refused as soon as it passes the limit.
"""
import json
from typing import Callable, Dict

from fastapi import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

class BodyTooLarge(HTTPException):
    def __init__(self, limit: int):
        super().__init__(413, f"request body is larger than {limit:,} bytes")

class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, limits: Dict[str, Callable[[], int]]):
        # path -> limit in bytes, read per request (tests lower it)
        self.app, self.limits = app, limits

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit_of = self.limits.get(scope.get("path", "")) if scope["type"] == "http" else None
        if limit_of is None:
            return await self.app(scope, receive, send)
        limit = limit_of()

        async def refuse() -> None:
            await send({"type": "http.response.start", "status": 413,
                        "headers": [(b"content-type", b"application/json"), (b"connection", b"close")]})
            await send({"type": "http.response.body", "body": json.dumps({"detail": BodyTooLarge(limit).detail}).encode()})

        length = dict(scope.get("headers") or []).get(b"content-length")
        try:
            declared = int(length) if length is not None else None
        except ValueError:
            declared = None
        if declared is not None and declared > limit:
            return await refuse()

        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # FastAPI re-raises an HTTPException raised while it reads the body (any other error becomes a 400)
                    raise BodyTooLarge(limit)
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except BodyTooLarge:
            if started:
                raise
            await refuse()
