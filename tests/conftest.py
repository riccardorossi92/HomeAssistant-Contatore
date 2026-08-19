"""Fixture condivise per i test.

pytest_plugins/enable_custom_integrations è il pattern standard richiesto da
pytest-homeassistant-custom-component perché Home Assistant, nei test, carichi
davvero questa integrazione custom invece di ignorarla.
"""
import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Applicato automaticamente a tutti i test che usano il fixture 'hass'."""
    yield
