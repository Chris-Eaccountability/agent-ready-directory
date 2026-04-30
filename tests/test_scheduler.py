"""
test_scheduler.py — Tests for the in-process scheduler.

Specifically covers the weekly verifier cron added for Task #22:
"directory cron + freshness display + badge tightening". Without this cron
the "Checks run weekly" claim on llms.txt and the company page is a lie.
"""

import os
from unittest.mock import AsyncMock, patch

import pytest


class TestSchedulerRegistration:
    """The scheduler must register both the nightly backup AND the weekly
    verifier. Asserting on job ids guards against a future refactor that
    quietly drops the verifier and breaks the freshness claim again."""

    @pytest.mark.asyncio
    async def test_start_registers_verify_all_weekly(self):
        # AsyncIOScheduler binds to the running loop on .start(), so this
        # test must run inside an asyncio loop.
        from app import scheduler as sched_mod

        sched_mod._scheduler = None
        try:
            sched = sched_mod.start()
            assert sched is not None, "scheduler should start when enabled"
            ids = {j.id for j in sched.get_jobs()}
            assert "verify_all_weekly" in ids
            assert "sqlite_backup_daily" in ids
        finally:
            sched_mod.stop()

    def test_start_disabled_when_env_false(self):
        from app import scheduler as sched_mod

        sched_mod._scheduler = None
        with patch.dict(os.environ, {"DIRECTORY_SCHEDULER_ENABLED": "false"}):
            assert sched_mod.start() is None


class TestVerifierJob:
    """The _job_verify_all coroutine is the body of the weekly cron. It must:
      - skip cleanly when DIRECTORY_VERIFIER_ENABLED=false
      - call verify_all and record the timestamp on success
      - swallow exceptions so the scheduler stays alive
    """

    @pytest.mark.asyncio
    async def test_skips_when_disabled(self, db_conn):
        from app import scheduler as sched_mod

        sched_mod._last_verifier_run_at = None
        sched_mod._last_verifier_count = None

        with patch.dict(os.environ, {"DIRECTORY_VERIFIER_ENABLED": "false"}):
            with patch("app.verifier.verify_all", new=AsyncMock()) as mock_va:
                await sched_mod._job_verify_all()
                mock_va.assert_not_awaited()
        assert sched_mod._last_verifier_run_at is None

    @pytest.mark.asyncio
    async def test_records_timestamp_on_success(self, db_conn):
        from app import scheduler as sched_mod

        sched_mod._last_verifier_run_at = None
        sched_mod._last_verifier_count = None

        # Two-company stub return so we can assert the count plumbs through.
        mock_results = {"acme": {"llms_txt": True}, "beta": {"llms_txt": False}}
        with patch("app.verifier.verify_all", new=AsyncMock(return_value=mock_results)):
            await sched_mod._job_verify_all()

        assert sched_mod._last_verifier_run_at is not None
        assert sched_mod._last_verifier_count == 2

    @pytest.mark.asyncio
    async def test_swallows_verifier_exception(self, db_conn):
        """Cron must not propagate — an exception from one weekly run shouldn't
        kill the scheduler."""
        from app import scheduler as sched_mod

        sched_mod._last_verifier_run_at = None
        with patch("app.verifier.verify_all",
                   new=AsyncMock(side_effect=RuntimeError("network down"))):
            await sched_mod._job_verify_all()  # must not raise
        assert sched_mod._last_verifier_run_at is None  # not recorded on failure


class TestHealthSurfacesVerifierFreshness:
    """The /health endpoint surfaces last_verifier_run_at so the public site
    can render "Last weekly sweep: Xd ago" without an extra DB hop. This test
    asserts the field exists in the response shape; freshness logic itself
    lives in client-side JS."""

    def test_health_includes_verifier_run_field(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "last_verifier_run_at" in data
        # Initially null — cron hasn't fired yet in tests.
        assert data["last_verifier_run_at"] is None

    def test_health_includes_oldest_check_at(self, client, seeded_db):
        """oldest_check_at lower-bounds when the last full sweep completed.
        Used by the public site's 'Last weekly sweep' indicator so the claim
        survives Fly machine restarts (unlike last_verifier_run_at)."""
        from datetime import datetime, timezone, timedelta

        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        new = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        # Two companies, different last_checked_at — MIN should win.
        seeded_db.execute(
            "UPDATE companies SET last_checked_at = ? WHERE id = 1", (old,)
        )
        seeded_db.execute(
            "UPDATE companies SET last_checked_at = ? WHERE id = 2", (new,)
        )
        seeded_db.commit()

        data = client.get("/health").json()
        assert data["oldest_check_at"] == old
        assert data["last_verification_run"] == new

    def test_health_sweep_status_ok_when_fresh(self, client, seeded_db):
        from datetime import datetime, timezone, timedelta
        recent = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        seeded_db.execute("UPDATE companies SET last_checked_at = ?", (recent,))
        seeded_db.commit()
        assert client.get("/health").json()["sweep_status"] == "ok"

    def test_health_sweep_status_stale_past_threshold(self, client, seeded_db):
        from datetime import datetime, timezone, timedelta
        ancient = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        seeded_db.execute("UPDATE companies SET last_checked_at = ?", (ancient,))
        seeded_db.commit()
        assert client.get("/health").json()["sweep_status"] == "stale"

    def test_health_sweep_status_no_runs_yet(self, client, seeded_db):
        seeded_db.execute("UPDATE companies SET last_checked_at = NULL")
        seeded_db.commit()
        assert client.get("/health").json()["sweep_status"] == "no_runs_yet"

    def test_health_oldest_check_ignores_null_and_deleted(self, client, seeded_db):
        """A NULL last_checked_at (e.g. brand-new submission not yet checked)
        must not pull oldest_check_at to null and overstate stale-ness.
        Soft-deleted rows must also be excluded."""
        from datetime import datetime, timezone, timedelta

        old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        ancient = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
        seeded_db.execute(
            "UPDATE companies SET last_checked_at = ? WHERE id = 1", (old,)
        )
        seeded_db.execute(
            "UPDATE companies SET last_checked_at = NULL WHERE id = 2"
        )
        seeded_db.execute(
            "UPDATE companies SET last_checked_at = ?, status = 'deleted' "
            "WHERE id = 3",
            (ancient,),
        )
        seeded_db.commit()

        data = client.get("/health").json()
        # NULL row excluded; deleted ancient row excluded; only id=1 counts.
        assert data["oldest_check_at"] == old
