"""Test end-to-end della logica di coda / ticket del PcfCoordinator.

Usano l'harness in conftest.py (FakePcfApi + statistiche/parsing patchati) e
freezegun per fissare data e ora. Verificano il comportamento osservabile -
quali richieste partono, cosa finisce in coda - non i dettagli interni.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from freezegun import freeze_time

from custom_components.contatore_letture.distributors.pcf_common.api import PcfApiError
from custom_components.contatore_letture.distributors.pcf_common.const import (
    CONF_DATA_INSTALLAZIONE,
    CONF_GIORNI_DA_RIPROVARE,
    CONF_PENDING_DATA_A,
    CONF_PENDING_DATA_DA,
    CONF_PENDING_IS_BACKFILL,
    CONF_PENDING_TICKET,
    FASE_GIORNALIERO,
    RITARDO_DATI_GIORNI,
)

from .conftest import curva

# Un momento in cui le richieste sono permesse (ora >= ORA_MINIMA_RICHIESTA).
ORA_SERA = "2026-09-15 20:00:00"
OGGI = date(2026, 9, 15)
ATTESO = OGGI - timedelta(days=RITARDO_DATI_GIORNI)

pytestmark = pytest.mark.usefixtures("pcf_io")


async def _aggiorna(hass, coordinator):
    """Un ciclo del coordinator + attesa del task di polling in background."""
    await coordinator.async_refresh()
    await hass.async_block_till_done()


async def test_primo_avvio_richiede_il_giorno_atteso(hass, make_pcf_coordinator, pcf_io):
    """Senza data di installazione il coordinator chiede subito 'atteso',
    saltando il vincolo orario, e registra la data di installazione."""
    pcf_io.parse_zip.return_value = curva(ATTESO)
    coordinator = make_pcf_coordinator()

    with freeze_time(ORA_SERA):
        await _aggiorna(hass, coordinator)

    assert coordinator.api.request_export.await_count == 1
    da, a = coordinator.api.request_export.await_args.args[:2]
    assert (da, a) == (ATTESO, ATTESO)
    assert coordinator._leggi_coda() == {}
    assert coordinator._entry.data.get(CONF_DATA_INSTALLAZIONE) == OGGI.isoformat()


async def test_giorno_mancante_nel_file_finisce_in_coda(hass, make_pcf_coordinator, pcf_io):
    """Se il file non contiene il giorno richiesto, quel giorno va in coda
    (tentativo 1) invece di andare perso."""
    pcf_io.parse_zip.return_value = {}  # file vuoto: nessun dato per 'atteso'
    coordinator = make_pcf_coordinator(data={CONF_DATA_INSTALLAZIONE: "2026-01-01"})

    with freeze_time(ORA_SERA):
        await _aggiorna(hass, coordinator)

    assert coordinator._leggi_coda() == {ATTESO.isoformat(): 1}


async def test_arretrato_in_coda_richiesto_in_un_unico_intervallo(
    hass, make_pcf_coordinator, pcf_io
):
    """Con un arretrato in coda, il ciclo giornaliero chiede un solo
    intervallo (dall'arretrato piu' vecchio fino ad 'atteso'), non una
    richiesta per giorno."""
    arretrato = ATTESO - timedelta(days=3)
    pcf_io.parse_zip.return_value = curva(
        *(arretrato + timedelta(days=i) for i in range(4))  # arretrato..ATTESO
    )
    coordinator = make_pcf_coordinator(
        data={
            CONF_DATA_INSTALLAZIONE: "2026-01-01",
            CONF_GIORNI_DA_RIPROVARE: {arretrato.isoformat(): 1},
        }
    )

    with freeze_time(ORA_SERA):
        await _aggiorna(hass, coordinator)

    assert coordinator.api.request_export.await_count == 1
    da, a = coordinator.api.request_export.await_args.args[:2]
    assert (da, a) == (arretrato, ATTESO)
    # Il file conteneva tutto l'intervallo: la coda si svuota.
    assert coordinator._leggi_coda() == {}


async def test_ticket_in_sospeso_ripreso_senza_nuovo_export(
    hass, make_pcf_coordinator, pcf_io
):
    """Un CONF_PENDING_TICKET salvato da un ciclo precedente viene ripreso
    direttamente con requestResult: niente nuova requestExport, e a import
    riuscito il ticket viene ripulito dalla entry."""
    giorno = ATTESO
    pcf_io.parse_zip.return_value = curva(giorno)
    coordinator = make_pcf_coordinator(
        data={
            CONF_DATA_INSTALLAZIONE: "2026-01-01",
            CONF_PENDING_TICKET: "T-OLD",
            CONF_PENDING_DATA_DA: giorno.isoformat(),
            CONF_PENDING_DATA_A: giorno.isoformat(),
            CONF_PENDING_IS_BACKFILL: FASE_GIORNALIERO,
        }
    )

    with freeze_time(ORA_SERA):
        await _aggiorna(hass, coordinator)

    assert coordinator.api.request_export.await_count == 0
    assert coordinator.api.request_result.await_count == 1
    assert coordinator.api.request_result.await_args.args[0] == "T-OLD"
    assert CONF_PENDING_TICKET not in coordinator._entry.data


async def test_export_rifiutata_accoda_i_giorni_e_segnala_fallimento(
    hass, make_pcf_coordinator
):
    """Se requestExport viene rifiutata nel ciclo giornaliero, i giorni
    coinvolti vanno in coda (non persi) e l'aggiornamento risulta fallito."""
    coordinator = make_pcf_coordinator()
    coordinator.api.request_export.side_effect = PcfApiError("richiesta rifiutata")

    with freeze_time(ORA_SERA):
        await _aggiorna(hass, coordinator)

    assert coordinator.last_update_success is False
    assert coordinator._leggi_coda() == {ATTESO.isoformat(): 1}
