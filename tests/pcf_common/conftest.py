"""Fixture per i test che istanziano davvero il PcfCoordinator.

I test esistenti (test_api*.py, test_coordinator_dates.py) coprono funzioni
isolate. Questi invece esercitano il coordinator completo - coda dei giorni
da riprovare, ripresa di un ticket in sospeso, pianificazione oraria -
sostituendo rete e recorder con fake controllabili, cosi' restano
deterministici e senza I/O.

Che cosa viene sostituito:
- PcfApiClient -> FakePcfApi (i tre metodi che il coordinator chiama)
- pcf_common.statistics.async_get_ultima_data_disponibile / async_import_curva
  -> AsyncMock (patchati nel namespace del coordinator)
- pcf_common.api.parse_curve_zip -> Mock sincrono (il coordinator lo invoca
  via hass.async_add_executor_job, quindi NON deve essere async)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contatore_letture.const import DOMAIN
from custom_components.contatore_letture.distributors import duereti
from custom_components.contatore_letture.distributors.pcf_common import coordinator as coord_mod
from custom_components.contatore_letture.distributors.pcf_common.api import (
    CurvaPunto,
    RisultatoLetture,
)

POD_TEST = "IT001E00000001"
DF_TEST = "RSSMRA80A01H501U"


class FakePcfApi:
    """Sostituto duck-typed di PcfApiClient.

    Solo i tre metodi che il coordinator usa davvero. Sono AsyncMock, quindi
    un test puo' impostare return_value / side_effect e ispezionare
    await_args_list.
    """

    def __init__(self) -> None:
        self.async_assicura_token = AsyncMock(return_value=None)
        self.request_export = AsyncMock(return_value="TICKET-1")
        self.request_result = AsyncMock(return_value=b"<zip fittizio>")


def curva(*giorni: date, pod: str = POD_TEST, kwh: float = 1.0) -> dict[str, RisultatoLetture]:
    """Risultato tipo parse_curve_zip: un punto (a mezzanotte) per ogni giorno.

    Serve a simulare cosa contiene il file restituito dal distributore: il
    coordinator confronta i giorni presenti nel file con quelli richiesti per
    decidere cosa togliere/mettere in coda.
    """
    punti = [
        CurvaPunto(timestamp=datetime(g.year, g.month, g.day), valore_kwh=kwh)
        for g in giorni
    ]
    return {pod: RisultatoLetture(pod=pod, punti=punti)}


@dataclass
class PcfIo:
    """Handle sui fake del layer statistiche/parsing, per i test che vogliono
    cambiarne il comportamento."""

    ultima_data: AsyncMock
    import_curva: AsyncMock
    parse_zip: Mock


@pytest.fixture
def pcf_io(monkeypatch) -> PcfIo:
    """Sostituisce statistiche e parsing nel namespace del coordinator.

    Default: nessuna serie gia' presente (ultima_data -> None), import che
    restituisce l'ultima data dei punti passati, parse che restituisce un
    file vuoto. I test sovrascrivono solo cio' che serve.
    """
    ultima_data = AsyncMock(return_value=None)

    def _import(hass, pod, ris, **_kw):
        return max((p.timestamp.date() for p in ris.punti), default=None)

    import_curva = AsyncMock(side_effect=_import)
    parse_zip = Mock(return_value={})

    monkeypatch.setattr(coord_mod, "async_get_ultima_data_disponibile", ultima_data)
    monkeypatch.setattr(coord_mod, "async_import_curva", import_curva)
    monkeypatch.setattr(coord_mod, "parse_curve_zip", parse_zip)

    return PcfIo(ultima_data=ultima_data, import_curva=import_curva, parse_zip=parse_zip)


@pytest.fixture
def make_pcf_coordinator(hass):
    """Factory: costruisce un PcfCoordinator per Duereti con una MockConfigEntry
    gia' agganciata a hass e l'API sostituita da FakePcfApi.

    data/options: sovrascrivono/estendono i valori della entry.
    pods: lista di {"pod", "df"}; default un solo POD.
    """

    def _make(*, data=None, options=None, pods=None):
        pods = pods if pods is not None else [{"pod": POD_TEST, "df": DF_TEST}]
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                "distributor": "duereti",
                "client_id": "cid",
                "secret_id": "sid",
                "pods": pods,
                **(data or {}),
            },
            options=options or {},
        )
        entry.add_to_hass(hass)
        coordinator = duereti.create_coordinator(
            hass, entry, client_id="cid", secret_id="sid", pods=pods
        )
        coordinator.api = FakePcfApi()
        return coordinator

    return _make
