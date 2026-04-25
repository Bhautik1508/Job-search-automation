"""
Shared pytest config — nukes external API credentials so tests are
isolated from the developer's local .env.

backend.config reads provider keys at import time and binds them to
module-level constants; the provider clients then re-import those
constants. So we have to clear both ``os.environ`` *and* the already-
imported constants on each test.
"""

from __future__ import annotations

import os

import pytest


_EXTERNAL_API_ENV_VARS = (
    "APOLLO_API_KEY",
    "HUNTER_API_KEY",
    "APIFY_API_TOKEN",
    "GEMINI_API_KEY",
    "API_KEY",
)


@pytest.fixture(autouse=True)
def _isolate_external_api_keys(monkeypatch):
    for var in _EXTERNAL_API_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
        os.environ.pop(var, None)

    # The provider clients import these as module-level constants from
    # backend.config — clearing env alone isn't enough.
    import backend.config as cfg
    monkeypatch.setattr(cfg, "APOLLO_API_KEY", "", raising=False)
    monkeypatch.setattr(cfg, "HUNTER_API_KEY", "", raising=False)
    monkeypatch.setattr(cfg, "APIFY_API_TOKEN", "", raising=False)
    monkeypatch.setattr(cfg, "GEMINI_API_KEY", "", raising=False)
    monkeypatch.setattr(cfg, "API_KEY", "", raising=False)

    # Provider client modules re-imported the constants at their own
    # import time, so the names are already bound there too.
    for mod_name, attr in (
        ("backend.contacts.apollo_client", "APOLLO_API_KEY"),
        ("backend.contacts.hunter_client", "HUNTER_API_KEY"),
        ("backend.contacts.apify_linkedin_client", "APIFY_API_TOKEN"),
        ("backend.scoring.gemini_scorer", "GEMINI_API_KEY"),
        ("backend.outreach.generator", "GEMINI_API_KEY"),
    ):
        try:
            mod = __import__(mod_name, fromlist=[attr])
            if hasattr(mod, attr):
                monkeypatch.setattr(mod, attr, "", raising=False)
        except ImportError:
            continue

    yield
