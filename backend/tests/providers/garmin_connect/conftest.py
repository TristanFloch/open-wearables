"""Override DB-dependent autouse fixtures for pure unit tests."""

import pytest


@pytest.fixture(autouse=True)
def set_factory_session():
    """No-op override: these tests don't need a database."""
    yield
