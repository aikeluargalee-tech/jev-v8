import os
import re
import pytest
from pathlib import Path
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app

ROOT_DIR = Path(__file__).resolve().parent.parent


def test_frontend_bundle_has_no_secrets():
    """
    AC-03: Secrets never in browser.
    Audits static assets and templates. Greps for forbidden secret patterns.
    """
    forbidden_patterns = [
        re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{10,}", re.IGNORECASE),
        re.compile(r"TYPESAFE_API_KEY", re.IGNORECASE),
        re.compile(r"TWITTER_API_KEY", re.IGNORECASE),
        re.compile(r"API_KEY\s*=", re.IGNORECASE),
        re.compile(r"\.env", re.IGNORECASE)
    ]

    frontend_paths = [
        ROOT_DIR / "app" / "static",
        ROOT_DIR / "app" / "templates"
    ]

    violations = []
    for fdir in frontend_paths:
        if not fdir.exists():
            continue
        for root, _, files in os.walk(fdir):
            for file in files:
                if file.endswith((".js", ".html", ".css")):
                    file_path = Path(root) / file
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                    for pat in forbidden_patterns:
                        matches = pat.findall(content)
                        if matches:
                            violations.append(f"{file_path.name}: matched forbidden pattern '{pat.pattern}' -> {matches}")

    assert len(violations) == 0, f"Found secrets/credentials in frontend bundle: {violations}"


def test_no_trading_or_order_endpoints_in_codebase():
    """
    Product Boundary Check:
    Asserts zero order placement, zero trading keys, zero private balance reads.
    Greps all application source code for createOrder, create_order, fetchBalance, fetch_balance.
    """
    forbidden_terms = ["createOrder", "create_order", "fetchBalance", "fetch_balance"]
    app_dir = ROOT_DIR / "app"

    violations = []
    for root, _, files in os.walk(app_dir):
        for file in files:
            if file.endswith(".py"):
                file_path = Path(root) / file
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                for term in forbidden_terms:
                    if term in content:
                        violations.append(f"{file_path.name} contains forbidden execution term '{term}'")

    assert len(violations) == 0, f"Found trading execution endpoints in codebase: {violations}"


def test_host_is_local_only():
    """Boundary Check: Localhost binding strictly enforced."""
    assert settings.HOST == "127.0.0.1", f"Host must be 127.0.0.1, got {settings.HOST}"


@pytest.mark.asyncio
async def test_csp_headers_enforced():
    """Security Check: Verify Content-Security-Policy headers on root response."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        res = await client.get("/")
        assert res.status_code == 200
        assert "content-security-policy" in res.headers
        csp = res.headers["content-security-policy"]
        assert "default-src 'self'" in csp
        assert res.headers.get("x-frame-options") == "DENY"


def test_ac09_typography_constraint():
    """AC-09: Minimum font size 16px enforced in stylesheet."""
    css_path = ROOT_DIR / "app" / "static" / "css" / "style.css"
    content = css_path.read_text(encoding="utf-8")
    assert "font-size: 16px;" in content, "style.css must enforce 16px minimum font size"
