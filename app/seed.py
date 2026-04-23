"""
seed.py — Initial seed data for the Agent-Ready Directory.

Called automatically on startup when the companies table is empty.
"""

import sqlite3
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------
SEED_COMPANIES = [
    {
        "slug": "fathom-smart-auger",
        "name": "FATHOM / Smart Auger Technologies",
        "domain": "smartauger.tech",
        "category": "aec",
        "description": (
            "AI-powered underground utility detection and mapping. "
            "Smart Auger Technologies deploys LLM-ready surfaces for AEC workflows."
        ),
        "website_url": "https://smartauger.tech",
        "elephant_verified": 1,
    },
    {
        "slug": "elephant-accountability",
        "name": "Elephant Accountability",
        "domain": "eaccountability.org",
        "category": "consulting",
        "description": (
            "LLM SEO for B2B SaaS. Elephant Accountability helps companies "
            "build agent-discovery infrastructure so LLMs can find and cite them."
        ),
        "website_url": "https://eaccountability.org",
        "elephant_verified": 1,
    },
    {
        "slug": "bentley-systems",
        "name": "Bentley Systems",
        "domain": "bentley.com",
        "category": "aec",
        "description": (
            "Infrastructure engineering software for advancing the world's infrastructure."
        ),
        "website_url": "https://bentley.com",
        "elephant_verified": 0,
    },
    {
        "slug": "exodigo",
        "name": "Exodigo",
        "domain": "exodigo.com",
        "category": "aec",
        "description": (
            "Non-intrusive underground mapping platform using AI and multi-sensor fusion."
        ),
        "website_url": "https://exodigo.com",
        "elephant_verified": 0,
    },
    {
        "slug": "procore",
        "name": "Procore",
        "domain": "procore.com",
        "category": "aec",
        "description": (
            "Construction management software connecting every project, team, and process."
        ),
        "website_url": "https://procore.com",
        "elephant_verified": 0,
    },
    {
        "slug": "autodesk",
        "name": "Autodesk",
        "domain": "autodesk.com",
        "category": "aec",
        "description": (
            "3D design, engineering, and construction software for architecture, "
            "engineering, and construction professionals."
        ),
        "website_url": "https://autodesk.com",
        "elephant_verified": 0,
    },
    {
        "slug": "trimble",
        "name": "Trimble",
        "domain": "trimble.com",
        "category": "aec",
        "description": (
            "Technology solutions for construction, geospatial, and transportation industries."
        ),
        "website_url": "https://trimble.com",
        "elephant_verified": 0,
    },
    {
        "slug": "screening-eagle",
        "name": "Screening Eagle",
        "domain": "screeningeagle.com",
        "category": "aec",
        "description": (
            "Non-destructive testing and infrastructure inspection technology "
            "combining hardware, software, and AI analytics."
        ),
        "website_url": "https://screeningeagle.com",
        "elephant_verified": 0,
    },
    {
        "slug": "geolitix",
        "name": "Geolitix",
        "domain": "geolitix.com",
        "category": "aec",
        "description": (
            "AI-driven geospatial analytics for subsurface infrastructure management."
        ),
        "website_url": "https://geolitix.com",
        "elephant_verified": 0,
    },
    {
        "slug": "gssi",
        "name": "GSSI",
        "domain": "geophysical.com",
        "category": "aec",
        "description": (
            "Geophysical Survey Systems Inc. — ground penetrating radar technology "
            "for subsurface investigation and utility locating."
        ),
        "website_url": "https://geophysical.com",
        "elephant_verified": 0,
    },
]

SURFACES = ["llms_txt", "mcp", "a2a", "ucp", "schema_org"]


def run_seed(conn: sqlite3.Connection) -> int:
    """
    Insert seed companies (and blank surface_status rows) if the table is empty.

    Returns the number of companies inserted (0 if already seeded).
    """
    row = conn.execute("SELECT COUNT(*) as cnt FROM companies").fetchone()
    if row["cnt"] > 0:
        return 0

    now = datetime.now(timezone.utc).isoformat()

    for company in SEED_COMPANIES:
        conn.execute(
            """
            INSERT INTO companies
                (slug, name, domain, logo_url, category, description,
                 website_url, submitted_by_email, submitted_at, status,
                 elephant_verified, last_checked_at, created_at, updated_at)
            VALUES
                (:slug, :name, :domain, :logo_url, :category, :description,
                 :website_url, :submitted_by_email, :submitted_at, :status,
                 :elephant_verified, :last_checked_at, :created_at, :updated_at)
            """,
            {
                "slug": company["slug"],
                "name": company["name"],
                "domain": company["domain"],
                "logo_url": company.get("logo_url"),
                "category": company.get("category"),
                "description": company.get("description"),
                "website_url": company.get("website_url"),
                "submitted_by_email": None,
                "submitted_at": now,
                "status": "verified",
                "elephant_verified": company.get("elephant_verified", 0),
                "last_checked_at": None,
                "created_at": now,
                "updated_at": now,
            },
        )
        company_id = conn.execute(
            "SELECT id FROM companies WHERE slug = ?", (company["slug"],)
        ).fetchone()["id"]

        for surface in SURFACES:
            conn.execute(
                """
                INSERT OR IGNORE INTO surface_status
                    (company_id, surface, verified, endpoint_url,
                     last_checked_at, last_verified_at)
                VALUES (?, ?, 0, NULL, NULL, NULL)
                """,
                (company_id, surface),
            )

    conn.commit()
    return len(SEED_COMPANIES)
