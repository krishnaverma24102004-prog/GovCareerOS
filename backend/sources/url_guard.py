"""
One URL check for every outbound fetch (listing pages, robots.txt, documents, every redirect hop).

A request may only go to the source's official domain (or a subdomain of it), over https
(plain http only when the source's own recruitment_url is http), and never to a private,
loopback, link-local or otherwise non-public address, including after DNS resolution.

`install(client, default)` adds the check as an httpx request hook, which httpx also runs for
each redirect hop. A request carries its source's policy in `extensions[POLICY]`; without one
the client's default applies, and a request with neither is refused.
"""
import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

POLICY = "govcareeros.url_policy"

class UrlRefused(httpx.RequestError):
    """The URL is outside what the source is allowed to fetch. Never retried or worked around."""

def _host(url: str) -> str:
    return (urlparse(url).hostname or "").rstrip(".").lower()

def site(host: str) -> str:
    host = host.rstrip(".").lower()
    return host[4:] if host.startswith("www.") else host

def _non_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%", 1)[0])
    return not addr.is_global or addr.is_multicast

def literal_non_public(url: str) -> bool:
    host = _host(url)
    try:
        return host == "localhost" or host.endswith(".localhost") or _non_public(host)
    except ValueError:
        return False

# Official aggregators (NCS, Employment News) link notices hosted on other government domains
GOVERNMENT_SUFFIXES = ("gov.in", "nic.in")

@dataclass(frozen=True)
class UrlPolicy:
    domain: str
    allow_http: bool = False
    extra_suffixes: tuple = ()  # further domains allowed besides `domain` (and their subdomains)

    @classmethod
    def for_source(cls, official_domain: str, recruitment_url: str, category: Optional[str] = None) -> "UrlPolicy":
        extra = GOVERNMENT_SUFFIXES if category == "OFFICIAL_AGGREGATOR" else ()
        return cls(site(official_domain), urlparse(recruitment_url).scheme == "http", extra)

    @classmethod
    def from_url(cls, url: str) -> "UrlPolicy":
        """For a fetch that starts from a configured official URL with no stored source row."""
        return cls(site(_host(url)), urlparse(url).scheme == "http")

    def refusal(self, url: str) -> Optional[str]:
        scheme, host = urlparse(url).scheme, _host(url)
        if scheme != "https" and not (scheme == "http" and self.allow_http):
            return f"scheme {scheme or 'none'!r} not allowed"
        if literal_non_public(url):  # names are checked after DNS resolution by GuardedTransport
            return f"non-public address {host}"
        allowed = [d for d in (self.domain, *self.extra_suffixes) if d]
        if not any(host == d or host.endswith("." + d) for d in allowed):
            return f"host {host!r} is outside {' / '.join(allowed) or 'no domain'!r}"
        return None

    def allows(self, url: str) -> bool:
        return self.refusal(url) is None

    def check(self, url: str, request: Optional[httpx.Request] = None) -> None:
        reason = self.refusal(url)
        if reason:
            raise UrlRefused(f"refused {url}: {reason}", request=request)

def install(client: httpx.AsyncClient, default: Optional[UrlPolicy] = None) -> httpx.AsyncClient:
    async def hook(request: httpx.Request) -> None:
        policy = request.extensions.get(POLICY) or default
        if policy is None:
            raise UrlRefused(f"refused {request.url}: no source policy for this request", request=request)
        policy.check(str(request.url), request)

    hooks = client.event_hooks
    if not any(getattr(h, "_url_guard", False) for h in hooks["request"]):
        hook._url_guard = True
        client.event_hooks = {**hooks, "request": [*hooks["request"], hook]}
    return client

Resolver = Callable[[str, int], Awaitable[List[str]]]

async def _resolve(host: str, port: int) -> List[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]

class GuardedTransport(httpx.AsyncHTTPTransport):
    """Refuses a connection when any address the host name resolves to is non-public."""

    def __init__(self, *args, resolve: Resolver = _resolve, **kwargs):
        super().__init__(*args, **kwargs)
        self._resolve = resolve

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        try:
            addrs = await self._resolve(host, request.url.port or (443 if request.url.scheme == "https" else 80))
        except OSError as e:
            raise httpx.ConnectError(f"cannot resolve {host}: {e}", request=request) from e
        bad = [a for a in addrs if _non_public(a)]
        if bad:
            raise UrlRefused(f"refused {request.url}: {host} resolves to non-public {bad[0]}", request=request)
        return await super().handle_async_request(request)

class Robots:
    """robots.txt per scheme+host, fetched through the same guarded client."""

    def __init__(self, client: httpx.AsyncClient, user_agent: str):
        self.client, self.user_agent = client, user_agent
        self._cache: Dict[str, Optional[RobotFileParser]] = {}

    async def allows(self, url: str, policy: Optional[UrlPolicy] = None) -> bool:
        parts = urlparse(url)
        key = f"{parts.scheme}://{parts.netloc}"
        if key not in self._cache:
            parser = None
            try:
                resp = await self.client.get(key + "/robots.txt", timeout=15, extensions={POLICY: policy} if policy else None)
                if resp.status_code == 200 and "text/html" not in resp.headers.get("content-type", ""):
                    parser = RobotFileParser()
                    parser.parse(resp.text.splitlines())
            except UrlRefused:
                raise
            except httpx.HTTPError:
                pass  # no reachable robots.txt: nothing disallowed
            self._cache[key] = parser
        parser = self._cache[key]
        return parser is None or parser.can_fetch(self.user_agent, url)

    async def sitemaps(self, url: str, policy: Optional[UrlPolicy] = None) -> List[str]:
        """`Sitemap:` lines of the host's robots.txt (loaded through `allows`)."""
        await self.allows(url, policy)
        parts = urlparse(url)
        parser = self._cache.get(f"{parts.scheme}://{parts.netloc}")
        return list(parser.site_maps() or []) if parser else []
