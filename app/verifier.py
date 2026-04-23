"""
verifier.py — Fetches agent-discovery endpoints for each company and updates
              surface_status rows in the database.

Surfaces checked:
  llms_txt   GET https://{domain}/llms.txt          200 + 'llms' in body (case-insensitive)
  mcp        GET https://{domain}/.well-known/mcp.json   200 + valid JSON with 'name' key
  a2a        GET https://{domain}/.well-known/agent.json 200 + valid JSON with 'name' key
  ucp        GET https://{domain}/.well-known/ucp.json   200 + valid JSON (any keys)
  schema_org GET https://{domain}/ — parse <script type="application/ld+json">

Timeout: 10 seconds per request.
User-Agent: AgentReadyDirectory/1.0 (+https://directory.eaccountability.org)
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
USER_AGENT = "AgentReadyDirectory/1.0 (+https://directory.eaccountability.org)"
TIMEOUT = 10.0  # seconds
SURFACES = ["llms_txt", "mcp", "a2a", "ucp", "schema_org"]


# ---------------------------------------------------------------------------
# Surface checks
# ---------------------------------------------------------------------------
async def _check_llms_txt(client: httpx.AsyncClient, domain: str) -> tuple[bool, str | None]:
    """Verify llms.txt: 200 response + 'llms' in body."""
    url = f"https://{domain}/llms.txt"
    try:
        resp = await client.get(url)
        if resp.status_code == 200 and "llms" in resp.text.lower():
            return True, url
    except Exception as exc:
        logger.debug("llms_txt check failed for %s: %s", domain, exc)
    return False, None


async def _check_mcp(client: httpx.AsyncClient, domain: str) -> tuple[bool, str | None]:
    """Verify MCP: 200 + valid JSON containing 'name' key."""
    url = f"https://{domain}/.well-known/mcp.json"
    try:
        resp = await client.get(url)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and "name" in data:
                return True, url
    except Exception as exc:
        logger.debug("mcp check failed for %s: %s", domain, exc)
    return False, None


async def _check_a2a(client: httpx.AsyncClient, domain: str) -> tuple[bool, str | None]:
    """Verify A2A: 200 + valid JSON containing 'name' key."""
    url = f"https://{domain}/.well-known/agent.json"
    try:
        resp = await client.get(url)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and "name" in data:
                return True, url
    except Exception as exc:
        logger.debug("a2a check failed for %s: %s", domain, exc)
    return False, None


async def _check_ucp(client: httpx.AsyncClient, domain: str) -> tuple[bool, str | None]:
    """Verify UCP: 200 + valid JSON (any structure)."""
    url = f"https://{domain}/.well-known/ucp.json"
    try:
        resp = await client.get(url)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, (dict, list)):
                return True, url
    except Exception as exc:
        logger.debug("ucp check failed for %s: %s", domain, exc)
    return False, None


async def _check_schema_org(client: httpx.AsyncClient, domain: str) -> tuple[bool, str | None]:
    """Verify Schema.org: fetch homepage, find <script type='application/ld+json'>."""
    url = f"https://{domain}/"
    try:
        resp = await client.get(url)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            scripts = soup.find_all("script", type="application/ld+json")
            for script in scripts:
                if script.string:
                    try:
                        data = json.loads(script.string)
                        if data:  # non-empty parsed JSON
                            return True, url
                    except json.JSONDecodeError:
                        continue
    except Exception as exc:
        logger.debug("schema_org check failed for %s: %s", domain, exc)
    return False, None


# ---------------------------------------------------------------------------
# Main verification function
# ---------------------------------------------------------------------------
async def verify_company(
    company: dict[str, Any],
    client: httpx.AsyncClient | None = None,
) -> dict[str, bool]:
    """
    Verify all surfaces for a company.

    Args:
        company: dict with at least 'domain' key.
        client:  Optional pre-built httpx.AsyncClient (for testing / batching).

    Returns:
        dict mapping surface name to bool, e.g.
        {'llms_txt': True, 'mcp': False, 'a2a': False, 'ucp': False, 'schema_org': True}
    """
    domain = company["domain"]
    headers = {"User-Agent": USER_AGENT}

    _own_client = client is None
    if _own_client:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(TIMEOUT),
            headers=headers,
            follow_redirects=True,
        )

    results: dict[str, bool] = {}
    endpoints: dict[str, str | None] = {}

    try:
        results["llms_txt"], endpoints["llms_txt"] = await _check_llms_txt(client, domain)
        results["mcp"], endpoints["mcp"] = await _check_mcp(client, domain)
        results["a2a"], endpoints["a2a"] = await _check_a2a(client, domain)
        results["ucp"], endpoints["ucp"] = await _check_ucp(client, domain)
        results["schema_org"], endpoints["schema_org"] = await _check_schema_org(client, domain)
    finally:
        if _own_client:
            await client.aclose()

    return results


def update_surface_statuses(
    conn: sqlite3.Connection,
    company_id: int,
    results: dict[str, bool],
    endpoints: dict[str, str | None] | None = None,
) -> None:
    """
    Persist surface verification results to the database.

    Args:
        conn:       Active SQLite connection.
        company_id: ID of the company row.
        results:    Surface name → verified bool.
        endpoints:  Surface name → URL where it was found (or None).
    """
    now = datetime.now(timezone.utc).isoformat()
    if endpoints is None:
        endpoints = {}

    for surface, verified in results.items():
        endpoint_url = endpoints.get(surface)
        last_verified_at = now if verified else None

        conn.execute(
            """
            INSERT INTO surface_status
                (company_id, surface, verified, endpoint_url, last_checked_at, last_verified_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_id, surface) DO UPDATE SET
                verified        = excluded.verified,
                endpoint_url    = excluded.endpoint_url,
                last_checked_at = excluded.last_checked_at,
                last_verified_at = excluded.last_verified_at
            """,
            (company_id, surface, int(verified), endpoint_url, now, last_verified_at),
        )

    # Update company.last_checked_at and updated_at
    conn.execute(
        "UPDATE companies SET last_checked_at = ?, updated_at = ? WHERE id = ?",
        (now, now, company_id),
    )
    conn.commit()


async def verify_company_and_persist(
    conn: sqlite3.Connection,
    company: dict[str, Any],
    client: httpx.AsyncClient | None = None,
) -> dict[str, bool]:
    """
    Verify a company and immediately persist results to DB.

    Returns the results dict.
    """
    domain = company["domain"]
    headers = {"User-Agent": USER_AGENT}
    company_id = company["id"]

    _own_client = client is None
    if _own_client:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(TIMEOUT),
            headers=headers,
            follow_redirects=True,
        )

    endpoints: dict[str, str | None] = {}
    results: dict[str, bool] = {}

    try:
        results["llms_txt"], endpoints["llms_txt"] = await _check_llms_txt(client, domain)
        results["mcp"], endpoints["mcp"] = await _check_mcp(client, domain)
        results["a2a"], endpoints["a2a"] = await _check_a2a(client, domain)
        results["ucp"], endpoints["ucp"] = await _check_ucp(client, domain)
        results["schema_org"], endpoints["schema_org"] = await _check_schema_org(client, domain)
    finally:
        if _own_client:
            await client.aclose()

    update_surface_statuses(conn, company_id, results, endpoints)
    return results


async def verify_all(
    conn: sqlite3.Connection,
    client: httpx.AsyncClient | None = None,
) -> dict[str, dict[str, bool]]:
    """
    Re-verify every company in the database.

    Returns a dict of {slug: results} for all companies processed.
    """
    rows = conn.execute(
        "SELECT id, slug, domain FROM companies WHERE status != 'deleted'"
    ).fetchall()

    headers = {"User-Agent": USER_AGENT}
    _own_client = client is None
    if _own_client:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(TIMEOUT),
            headers=headers,
            follow_redirects=True,
        )

    all_results: dict[str, dict[str, bool]] = {}
    try:
        for row in rows:
            company = dict(row)
            try:
                results = await verify_company_and_persist(conn, company, client=client)
                all_results[row["slug"]] = results
            except Exception as exc:
                logger.warning("Failed to verify %s: %s", row["slug"], exc)
                all_results[row["slug"]] = {s: False for s in SURFACES}
    finally:
        if _own_client:
            await client.aclose()

    return all_results
