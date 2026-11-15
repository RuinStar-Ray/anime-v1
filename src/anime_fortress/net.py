from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from anime_fortress.config import Settings
from anime_fortress.errors import RetryableTaskError, SourceError

_REDACTED_QUERY_KEYS = re.compile(
    r"(?i)(token|key|sig|signature|auth|password|secret|expires|policy|credential)"
)
_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def sanitize_url(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return "[invalid-url]"
    if not parts.query:
        return url
    query_parts = []
    for part in parts.query.split("&"):
        name = part.split("=", 1)[0]
        if _REDACTED_QUERY_KEYS.search(name):
            query_parts.append(f"{name}=[REDACTED]")
        else:
            query_parts.append(part)
    return parts._replace(query="&".join(query_parts)).geturl()


def is_forbidden_ip(address: str, *, allow_private: bool = False) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    if ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return True
    if ip.is_loopback:
        return not allow_private
    if ip.is_private or ip.is_link_local:
        return not allow_private
    return not ip.is_global


async def validate_http_url(url: str, *, allow_private: bool = False) -> str:
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise SourceError(f"Malformed URL: {exc}") from exc
    if parts.scheme not in {"http", "https"}:
        raise SourceError("Only http:// and https:// URLs are supported (file paths need the local adapter)")
    if not parts.hostname:
        raise SourceError("URL has no hostname")
    if parts.username or parts.password:
        raise SourceError(
            "Credentials in URLs are not allowed. Use headers_env to reference an environment variable."
        )
    host = parts.hostname.strip().lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        if not allow_private:
            raise SourceError("Local/private network URLs are blocked by configuration")
    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None
    if literal_ip is not None and is_forbidden_ip(str(literal_ip), allow_private=allow_private):
        raise SourceError("Private, loopback, or reserved IP addresses are blocked")
    if literal_ip is None:
        try:
            infos = await asyncio.to_thread(
                socket.getaddrinfo, host, parts.port or 443, type=socket.SOCK_STREAM
            )
        except socket.gaierror as exc:
            raise SourceError(f"Could not resolve source host {host}: {exc}") from exc
        for info in infos:
            address = info[4][0]
            if is_forbidden_ip(address, allow_private=allow_private):
                raise SourceError(
                    "Source host resolves to a private, loopback, or reserved address; blocked"
                )
    return url


def headers_from_env(variable_name: str | None, *, environ: dict[str, str] | None = None) -> dict[str, str]:
    if not variable_name:
        return {}
    env = environ if environ is not None else os.environ
    raw = env.get(variable_name)
    if not raw:
        raise SourceError(
            f"Required authentication headers are missing: environment variable {variable_name} is unset"
        )
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise SourceError(f"{variable_name} must contain a JSON object of HTTP headers") from exc
    if not isinstance(parsed, dict):
        raise SourceError(f"{variable_name} must contain a JSON object of HTTP headers")
    headers: dict[str, str] = {}
    for name, value in parsed.items():
        if not isinstance(name, str) or not _HEADER_NAME_RE.match(name):
            raise SourceError(f"Invalid HTTP header name in {variable_name}")
        text = str(value)
        if "\r" in text or "\n" in text:
            raise SourceError(f"Invalid HTTP header value in {variable_name}")
        headers[name] = text
    return headers


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    sha256: str
    size_bytes: int
    content_type: str | None


class SafeHttpClient:
    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        *,
        max_redirects: int = 5,
    ) -> None:
        self.settings = settings
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.http_timeout_seconds),
            follow_redirects=False,
        )
        self.max_redirects = max_redirects

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def __aenter__(self) -> SafeHttpClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def request(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        max_bytes: int | None = None,
        raise_for_status: bool = True,
    ) -> httpx.Response:
        current = await validate_http_url(
            url, allow_private=self.settings.allow_private_networks
        )
        for _ in range(self.max_redirects + 1):
            try:
                response = await self.client.request(
                    method, current, headers=headers, follow_redirects=False
                )
            except httpx.TimeoutException as exc:
                raise RetryableTaskError(f"Timed out fetching {sanitize_url(current)}") from exc
            except httpx.HTTPError as exc:
                raise RetryableTaskError(f"Network error fetching {sanitize_url(current)}: {exc}") from exc
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise SourceError(f"Redirect from {sanitize_url(current)} has no Location header")
                current = await validate_http_url(
                    urljoin(current, location),
                    allow_private=self.settings.allow_private_networks,
                )
                continue
            if not raise_for_status:
                return response
            if response.status_code in {401, 403}:
                raise SourceError(
                    f"Source denied access ({response.status_code}). Check authorization headers."
                )
            if response.status_code == 404:
                raise SourceError("Source URL was not found (HTTP 404)")
            if response.status_code == 429 or response.status_code >= 500:
                raise RetryableTaskError(
                    f"Source temporarily unavailable (HTTP {response.status_code})"
                )
            if response.status_code >= 400:
                raise SourceError(f"Source request failed with HTTP {response.status_code}")
            if max_bytes is not None:
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise SourceError("Source response exceeds the configured size limit")
            return response
        raise SourceError("Too many redirects while fetching the source")

    async def get_bytes(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        max_bytes: int = 2_000_000,
    ) -> tuple[bytes, str]:
        response = await self.request(url, headers=headers, max_bytes=max_bytes)
        chunks: list[bytes] = []
        total = 0
        try:
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise SourceError("Source response exceeds the configured size limit")
                chunks.append(chunk)
        finally:
            await response.aclose()
        return b"".join(chunks), str(response.url)

    async def download(
        self,
        url: str,
        destination: Path,
        *,
        headers: dict[str, str] | None = None,
        max_bytes: int | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> DownloadResult:
        cap = max_bytes or self.settings.max_download_mb * 1024 * 1024
        response = await self.request(url, headers=headers, max_bytes=cap)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_path = destination.with_suffix(destination.suffix + ".part")
        digest = hashlib.sha256()
        total = 0
        content_type = response.headers.get("content-type")
        try:
            with temp_path.open("wb") as handle:
                async for chunk in response.aiter_bytes(1024 * 256):
                    total += len(chunk)
                    if total > cap:
                        raise SourceError("Download exceeded the configured size limit")
                    digest.update(chunk)
                    handle.write(chunk)
                    if progress:
                        progress(total)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
        finally:
            await response.aclose()
        if total == 0:
            temp_path.unlink(missing_ok=True)
            raise SourceError("Source returned an empty file")
        temp_path.replace(destination)
        return DownloadResult(
            path=destination,
            sha256=digest.hexdigest(),
            size_bytes=total,
            content_type=content_type,
        )
