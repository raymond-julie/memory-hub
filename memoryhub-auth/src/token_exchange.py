import logging
import re
import time
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.errors import OAuthError
from src.models import OAuthClient

logger = logging.getLogger("memoryhub-auth.token_exchange")

_sa_token_cache: str | None = None
_tenant_cache: dict[str, tuple[str, float]] = {}
_k8s_client_cache: httpx.AsyncClient | None = None

SA_USERNAME_PATTERN = re.compile(r"^system:serviceaccount:([^:]+):([^:]+)$")


def _read_sa_token() -> str:
    global _sa_token_cache
    if _sa_token_cache is not None:
        return _sa_token_cache

    token_path = Path(settings.k8s_token_path)
    if not token_path.exists():
        raise OAuthError(
            500,
            "server_error",
            f"ServiceAccount token not found at {settings.k8s_token_path}",
        )

    _sa_token_cache = token_path.read_text().strip()
    logger.info("Loaded ServiceAccount token from %s", settings.k8s_token_path)
    return _sa_token_cache


def _get_k8s_client() -> httpx.AsyncClient:
    global _k8s_client_cache
    if _k8s_client_cache is not None:
        return _k8s_client_cache

    ca_path = Path(settings.k8s_ca_path)
    if ca_path.exists():
        _k8s_client_cache = httpx.AsyncClient(verify=str(ca_path))
        logger.info("Created K8s client with CA bundle from %s", settings.k8s_ca_path)
    else:
        _k8s_client_cache = httpx.AsyncClient(verify=True)
        logger.info("Created K8s client with default TLS verification")

    return _k8s_client_cache


async def validate_subject_token(subject_token: str) -> dict:
    sa_token = _read_sa_token()
    client = _get_k8s_client()

    url = f"{settings.k8s_api_server}/apis/authentication.k8s.io/v1/tokenreviews"
    payload = {
        "apiVersion": "authentication.k8s.io/v1",
        "kind": "TokenReview",
        "spec": {"token": subject_token},
    }
    headers = {
        "Authorization": f"Bearer {sa_token}",
        "Content-Type": "application/json",
    }

    try:
        response = await client.post(url, json=payload, headers=headers, timeout=10.0)
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        logger.error("TokenReview API returned %s: %s", e.response.status_code, e.response.text)
        raise OAuthError(
            401,
            "invalid_grant",
            f"Subject token validation failed: HTTP {e.response.status_code}",
        ) from None
    except httpx.RequestError as e:
        logger.error("TokenReview API request failed: %s", e)
        raise OAuthError(
            502,
            "server_error",
            f"Token validation unavailable: {type(e).__name__}",
        ) from None

    body = response.json()
    status = body.get("status", {})

    if not status.get("authenticated"):
        error_msg = status.get("error", "Authentication failed")
        logger.warning("TokenReview rejected token: %s", error_msg)
        raise OAuthError(
            401,
            "invalid_grant",
            f"Subject token validation failed: {error_msg}",
        )

    user = status.get("user", {})
    return {
        "username": user.get("username", ""),
        "groups": user.get("groups", []),
        "authenticated": True,
    }


def parse_service_account(username: str) -> tuple[str, str]:
    match = SA_USERNAME_PATTERN.match(username)
    if not match:
        raise OAuthError(
            400,
            "invalid_request",
            f"Subject token username must be a ServiceAccount (system:serviceaccount:ns:name), got: {username}",
        )
    return match.group(1), match.group(2)


async def resolve_tenant(namespace: str) -> str:
    now = time.time()

    if namespace in _tenant_cache:
        tenant_id, expires_at = _tenant_cache[namespace]
        if now < expires_at:
            logger.debug("Tenant cache hit for namespace %s: %s", namespace, tenant_id)
            return tenant_id

    sa_token = _read_sa_token()
    client = _get_k8s_client()

    url = f"{settings.k8s_api_server}/api/v1/namespaces/{namespace}"
    headers = {"Authorization": f"Bearer {sa_token}"}

    try:
        response = await client.get(url, headers=headers, timeout=5.0)
        response.raise_for_status()
        body = response.json()
        annotations = body.get("metadata", {}).get("annotations", {})
        tenant_id = annotations.get("memoryhub.redhat.com/tenant-id", settings.default_tenant_id)
        logger.info("Resolved namespace %s to tenant %s", namespace, tenant_id)
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        logger.warning(
            "Failed to resolve tenant for namespace %s: %s, using default",
            namespace,
            e,
        )
        tenant_id = settings.default_tenant_id

    _tenant_cache[namespace] = (tenant_id, now + settings.tenant_cache_ttl)
    return tenant_id


async def lookup_exchange_client(client_id: str, session: AsyncSession) -> OAuthClient:
    stmt = select(OAuthClient).where(
        OAuthClient.client_id == client_id,
        OAuthClient.active,
    )
    result = await session.execute(stmt)
    client = result.scalar_one_or_none()

    if client is None:
        raise OAuthError(401, "invalid_client", "Unknown or inactive client")

    return client
