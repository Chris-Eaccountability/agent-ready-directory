"""
server.py — FastAPI application for the Agent-Ready Directory.

Routes:
  Public static pages: /, /company/<slug>, /submit, /about
  API (public):  /api/companies, /api/companies/<slug>,
                 /api/submissions, /api/export.json, /api/export.csv,
                 /sitemap.xml, /robots.txt, /llms.txt, /health
  Admin (bearer token): /api/admin/verify-all,
                        /api/admin/companies/<slug>/elephant-verify,
                        DELETE /api/admin/companies/<slug>
"""

import csv
import hashlib
import io
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from . import __version__
from .db import get_db, init_db, get_connection
from .seed import run_seed
from .verifier import (
    verify_all,
    verify_company_and_persist,
    USER_AGENT,
    TIMEOUT,
    _check_llms_txt,
    _check_mcp,
    _check_a2a,
    _check_ucp,
    _check_schema_org,
    update_surface_statuses,
)

logger = logging.getLogger(__name__)

# Process start timestamp for /health uptime_seconds. Set at import time.
_PROCESS_STARTED_AT = datetime.now(timezone.utc)

# -----