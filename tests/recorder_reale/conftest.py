"""Il fixture recorder_mock di pytest-homeassistant-custom-component deve
partire PRIMA di hass, ma l'autouse del conftest radice richiede
enable_custom_integrations (quindi hass) per primo. Qui l'autouse è
ridefinito con l'ordine giusto."""
import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    yield
