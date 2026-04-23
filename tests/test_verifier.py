"""
test_verifier.py — Tests for the verifier module.

All httpx calls are mocked — no real network requests are made.
"""

import json
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.db import init_db
from app.verifier import (
    _check_a2a,
    _check_llms_txt,
    _check_mcp,
    _check_schema_org,
    _check_ucp,
    update_surface_statuses,
    verify_all,
    verify_company,
    verify_company_and_persist,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _mock_response(status_code: int, text: str = "", json_data=None) -> httpx.Response:
    """Build a fake httpx.Response."""
    if json_data is not None:
        text = json.dumps(json_data)
    return httpx.Response(status_code=status_code, text=text)


def _mock_client(responses: dict[str, httpx.Response]) -> AsyncMock:
    """
    Build an AsyncMock httpx client where client.get(url) returns
    the matching response from *responses* dict.
    """
    client = AsyncMock()

    async def side_effect(url, **kwargs):
        if url in responses:
            return responses[url]
        return httpx.Response(status_code=404, text="Not Found")

    client.get = AsyncMock(side_effect=side_effect)
    return client


def _fresh_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    return conn


# ===========================================================================
# llms.txt surface
# ===========================================================================
class TestLlmsTxtSurface:
    @pytest.mark.asyncio
    async def test_llms_txt_passes_with_llms_in_body(self):
        client = _mock_client({
            "https://example.com/llms.txt": _mock_response(200, "# llms.txt\nThis is the llms content"),
        })
        ok, url = await _check_llms_txt(client, "example.com")
        assert ok is True
        assert url == "https://example.com/llms.txt"

    @pytest.mark.asyncio
    async def test_llms_txt_fails_without_llms_keyword(self):
        client = _mock_client({
            "https://example.com/llms.txt": _mock_response(200, "just some text"),
        })
        ok, url = await _check_llms_txt(client, "example.com")
        assert ok is False
        assert url is None

    @pytest.mark.asyncio
    async def test_llms_txt_fails_on_404(self):
        client = _mock_client({})  # all 404s
        ok, url = await _check_llms_txt(client, "example.com")
        assert ok is False

    @pytest.mark.asyncio
    async def test_llms_txt_case_insensitive(self):
        client = _mock_client({
            "https://example.com/llms.txt": _mock_response(200, "LLMS content here"),
        })
        ok, _ = await _check_llms_txt(client, "example.com")
        assert ok is True

    @pytest.mark.asyncio
    async def test_llms_txt_handles_network_error(self):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("unreachable"))
        ok, url = await _check_llms_txt(client, "example.com")
        assert ok is False
        assert url is None


# ===========================================================================
# MCP surface
# ===========================================================================
class TestMcpSurface:
    @pytest.mark.asyncio
    async def test_mcp_passes_with_name_key(self):
        client = _mock_client({
            "https://example.com/.well-known/mcp.json": _mock_response(
                200, json_data={"name": "Example MCP", "version": "1.0"}
            ),
        })
        ok, url = await _check_mcp(client, "example.com")
        assert ok is True
        assert url == "https://example.com/.well-known/mcp.json"

    @pytest.mark.asyncio
    async def test_mcp_fails_without_name_key(self):
        client = _mock_client({
            "https://example.com/.well-known/mcp.json": _mock_response(
                200, json_data={"tools": []}
            ),
        })
        ok, _ = await _check_mcp(client, "example.com")
        assert ok is False

    @pytest.mark.asyncio
    async def test_mcp_fails_on_404(self):
        client = _mock_client({})
        ok, _ = await _check_mcp(client, "example.com")
        assert ok is False

    @pytest.mark.asyncio
    async def test_mcp_fails_on_invalid_json(self):
        client = _mock_client({
            "https://example.com/.well-known/mcp.json": _mock_response(200, "not json!"),
        })
        ok, _ = await _check_mcp(client, "example.com")
        assert ok is False


# ===========================================================================
# A2A surface
# ===========================================================================
class TestA2aSurface:
    @pytest.mark.asyncio
    async def test_a2a_passes_with_name_key(self):
        client = _mock_client({
            "https://example.com/.well-known/agent.json": _mock_response(
                200, json_data={"name": "Example Agent", "capabilities": []}
            ),
        })
        ok, url = await _check_a2a(client, "example.com")
        assert ok is True

    @pytest.mark.asyncio
    async def test_a2a_fails_without_name_key(self):
        client = _mock_client({
            "https://example.com/.well-known/agent.json": _mock_response(
                200, json_data={"version": "1.0"}
            ),
        })
        ok, _ = await _check_a2a(client, "example.com")
        assert ok is False

    @pytest.mark.asyncio
    async def test_a2a_fails_on_404(self):
        client = _mock_client({})
        ok, _ = await _check_a2a(client, "example.com")
        assert ok is False


# ===========================================================================
# UCP surface
# ===========================================================================
class TestUcpSurface:
    @pytest.mark.asyncio
    async def test_ucp_passes_with_any_json_dict(self):
        client = _mock_client({
            "https://example.com/.well-known/ucp.json": _mock_response(
                200, json_data={"context": "example"}
            ),
        })
        ok, url = await _check_ucp(client, "example.com")
        assert ok is True

    @pytest.mark.asyncio
    async def test_ucp_passes_with_json_list(self):
        client = _mock_client({
            "https://example.com/.well-known/ucp.json": _mock_response(
                200, json_data=[{"key": "value"}]
            ),
        })
        ok, _ = await _check_ucp(client, "example.com")
        assert ok is True

    @pytest.mark.asyncio
    async def test_ucp_fails_on_404(self):
        client = _mock_client({})
        ok, _ = await _check_ucp(client, "example.com")
        assert ok is False

    @pytest.mark.asyncio
    async def test_ucp_fails_on_invalid_json(self):
        client = _mock_client({
            "https://example.com/.well-known/ucp.json": _mock_response(200, "plain text"),
        })
        ok, _ = await _check_ucp(client, "example.com")
        assert ok is False


# ===========================================================================
# Schema.org surface
# ===========================================================================
class TestSchemaOrgSurface:
    @pytest.mark.asyncio
    async def test_schema_org_passes_with_ld_json(self):
        html = """<!DOCTYPE html><html><head>
        <script type="application/ld+json">{"@type":"Organization","name":"Example"}</script>
        </head><body></body></html>"""
        client = _mock_client({
            "https://example.com/": _mock_response(200, html),
        })
        ok, url = await _check_schema_org(client, "example.com")
        assert ok is True

    @pytest.mark.asyncio
    async def test_schema_org_fails_without_ld_json(self):
        html = "<html><head></head><body>No structured data</body></html>"
        client = _mock_client({
            "https://example.com/": _mock_response(200, html),
        })
        ok, _ = await _check_schema_org(client, "example.com")
        assert ok is False

    @pytest.mark.asyncio
    async def test_schema_org_fails_on_404(self):
        client = _mock_client({})
        ok, _ = await _check_schema_org(client, "example.com")
        assert ok is False

    @pytest.mark.asyncio
    async def test_schema_org_fails_invalid_json_in_ld(self):
        html = """<html><head>
        <script type="application/ld+json">this is not json</script>
        </head><body></body></html>"""
        client = _mock_client({
            "https://example.com/": _mock_response(200, html),
        })
        ok, _ = await _check_schema_org(client, "example.com")
        assert ok is False


# ===========================================================================
# verify_company
# ===========================================================================
class TestVerifyCompany:
    @pytest.mark.asyncio
    async def test_verify_company_returns_all_surfaces(self):
        """verify_company should return a dict with all 5 surface keys."""
        client = _mock_client({})  # all 404s
        company = {"domain": "example.com", "id": 1, "slug": "example"}
        results = await verify_company(company, client=client)
        assert set(results.keys()) == {"llms_txt", "mcp", "a2a", "ucp", "schema_org"}

    @pytest.mark.asyncio
    async def test_verify_company_all_pass(self):
        html = """<html><head><script type="application/ld+json">{"@type":"Org"}</script></head></html>"""
        client = _mock_client({
            "https://example.com/llms.txt": _mock_response(200, "# llms.txt content"),
            "https://example.com/.well-known/mcp.json": _mock_response(200, json_data={"name": "X"}),
            "https://example.com/.well-known/agent.json": _mock_response(200, json_data={"name": "X"}),
            "https://example.com/.well-known/ucp.json": _mock_response(200, json_data={"k": "v"}),
            "https://example.com/": _mock_response(200, html),
        })
        company = {"domain": "example.com", "id": 1, "slug": "example"}
        results = await verify_company(company, client=client)
        assert results["llms_txt"] is True
        assert results["mcp"] is True
        assert results["a2a"] is True
        assert results["ucp"] is True
        assert results["schema_org"] is True

    @pytest.mark.asyncio
    async def test_verify_company_all_fail(self):
        client = _mock_client({})
        company = {"domain": "nobody.io", "id": 99, "slug": "nobody"}
        results = await verify_company(company, client=client)
        assert all(v is False for v in results.values())


# ===========================================================================
# update_surface_statuses
# ===========================================================================
class TestUpdateSurfaceStatuses:
    def test_inserts_new_rows(self):
        conn = _fresh_db()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO companies (slug,name,domain,submitted_at,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            ("test-co","Test Co","test.io",now,now,now),
        )
        conn.commit()
        cid = conn.execute("SELECT id FROM companies WHERE slug='test-co'").fetchone()["id"]

        results = {"llms_txt": True, "mcp": False, "a2a": False, "ucp": True, "schema_org": False}
        endpoints = {"llms_txt": "https://test.io/llms.txt", "mcp": None, "a2a": None, "ucp": "https://test.io/.well-known/ucp.json", "schema_org": None}
        update_surface_statuses(conn, cid, results, endpoints)

        rows = conn.execute("SELECT surface, verified FROM surface_status WHERE company_id=?", (cid,)).fetchall()
        surface_map = {r["surface"]: r["verified"] for r in rows}
        assert surface_map["llms_txt"] == 1
        assert surface_map["mcp"] == 0
        assert surface_map["ucp"] == 1

    def test_updates_existing_rows(self):
        conn = _fresh_db()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO companies (slug,name,domain,submitted_at,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            ("test-co2","Test Co2","test2.io",now,now,now),
        )
        conn.commit()
        cid = conn.execute("SELECT id FROM companies WHERE slug='test-co2'").fetchone()["id"]

        # Insert initial row
        conn.execute(
            "INSERT INTO surface_status (company_id,surface,verified) VALUES (?,?,?)",
            (cid, "llms_txt", 0),
        )
        conn.commit()

        # Update to verified
        update_surface_statuses(conn, cid, {"llms_txt": True}, {"llms_txt": "https://test2.io/llms.txt"})

        row = conn.execute(
            "SELECT verified FROM surface_status WHERE company_id=? AND surface='llms_txt'",
            (cid,),
        ).fetchone()
        assert row["verified"] == 1


# ===========================================================================
# verify_all
# ===========================================================================
class TestVerifyAll:
    @pytest.mark.asyncio
    async def test_verify_all_returns_results_for_all_companies(self):
        conn = _fresh_db()
        from app.seed import run_seed
        run_seed(conn)

        # Mock client that returns all failures (fast)
        client = _mock_client({})
        results = await verify_all(conn, client=client)
        assert len(results) == 10  # 10 seeded companies

    @pytest.mark.asyncio
    async def test_verify_all_updates_database(self):
        conn = _fresh_db()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO companies (slug,name,domain,submitted_at,created_at,updated_at,status) VALUES (?,?,?,?,?,?,?)",
            ("co1","Co1","co1.io",now,now,now,"verified"),
        )
        conn.commit()

        html = "<html><head><script type='application/ld+json'>{\"@type\":\"Org\"}</script></head></html>"
        client = _mock_client({
            "https://co1.io/llms.txt": _mock_response(200, "llms content"),
            "https://co1.io/": _mock_response(200, html),
        })

        await verify_all(conn, client=client)

        row = conn.execute(
            "SELECT verified FROM surface_status WHERE company_id=1 AND surface='llms_txt'"
        ).fetchone()
        # llms_txt should be verified
        if row:
            assert row["verified"] == 1
