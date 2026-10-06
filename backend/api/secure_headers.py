"""
Security headers on every API response.

- X-Content-Type-Options: nosniff, X-Frame-Options: DENY, Referrer-Policy: no-referrer on everything.
- A locked-down Content-Security-Policy on JSON responses: the API serves data, never pages, so a JSON body
  opened in a browser can load and run nothing. HTML (the /docs page, rendered resumes) keeps its own needs.
- Strict-Transport-Security only when the request came over https (HSTS on plain http is ignored by browsers and
  would only confuse a local run). Behind a proxy, uvicorn --proxy-headers sets the scheme from X-Forwarded-Proto.
A header the route already set is left as it is.
"""
from typing import Callable

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

JSON_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

class SecureHeadersMiddleware:
    def __init__(self, app: ASGIApp, hsts_seconds: Callable[[], int]):
        # read per request (tests change the setting)
        self.app, self.hsts_seconds = app, hsts_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        https = scope.get("scheme") == "https"

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Referrer-Policy", "no-referrer")
                content_type = headers.get("content-type", "").split(";")[0].strip().lower()
                if content_type == "application/json" or content_type.endswith("+json"):
                    headers.setdefault("Content-Security-Policy", JSON_CSP)
                max_age = self.hsts_seconds()
                if https and max_age > 0:
                    headers.setdefault("Strict-Transport-Security", f"max-age={max_age}; includeSubDomains")
            await send(message)

        await self.app(scope, receive, with_headers)
