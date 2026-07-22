from __future__ import annotations

import pytest
import structlog


@pytest.fixture(autouse=True)
def _isolate_structlog() -> object:
    structlog.reset_defaults()
    yield
    structlog.reset_defaults()
