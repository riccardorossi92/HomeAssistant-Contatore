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
    ABBANDONO_CODA_DOPO_GIORNI,
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
# Interpretato come UTC da freezegun: i test sotto fissano il fuso di hass a
# UTC (vedi _fuso_utc) perche' _prossima_richiesta confronta dt_util.now().hour
# con l'ora configurata.
ORA_SERA = "2026-09-15 20:00:00"
OGGI = date(2026, 9, 15)
ATTESO = OGGI - timedelta(days=RITARDO_DATI_GIORNI)

pytestmark = pytest.mark.usefixtures("pcf_io")


@pytest.fixture(autouse=True)
async def _fuso_utc(hass):
    """Ancora il fuso di hass a UTC: senza, dt_util.now() sfasa rispetto
    all'orario fissato con freeze_time e il vincolo orario scatta a sproposito."""
    await hass.config.async_set_time_zone("UTC")


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
    (con 'oggi' come data di primo inserimento) invece di andare perso."""
    pcf_io.parse_zip.return_value = {}  # file vuoto: nessun dato per 'atteso'
    coordinator = make_pcf_coordinator(data={CONF_DATA_INSTALLAZIONE: "2026-01-01"})

    with freeze_time(ORA_SERA):
        await _aggiorna(hass, coordinator)

    assert coordinator._leggi_coda() == {ATTESO.isoformat(): OGGI}


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
            # in coda da pochi giorni: non ancora da abbandonare
            CONF_GIORNI_DA_RIPROVARE: {arretrato.isoformat(): arretrato.isoformat()},
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
    assert coordinator._leggi_coda() == {ATTESO.isoformat(): OGGI}


async def test_coda_abbandona_i_giorni_troppo_vecchi(hass, make_pcf_coordinator):
    """Un giorno in coda da ABBANDONO_CODA_DOPO_GIORNI giorni o più viene
    scartato al primo aggiornamento della coda; quelli più recenti restano."""
    vecchio = (OGGI - timedelta(days=ABBANDONO_CODA_DOPO_GIORNI)).isoformat()
    recente = (OGGI - timedelta(days=1)).isoformat()
    coordinator = make_pcf_coordinator(
        data={
            CONF_GIORNI_DA_RIPROVARE: {
                "2026-08-20": vecchio,
                "2026-09-12": recente,
            }
        }
    )

    with freeze_time(ORA_SERA):
        # Un qualunque tocco alla coda passa da _scrivi_coda, che applica lo scarto.
        coordinator._accoda_giorno(date(2026, 9, 13))
        coda = coordinator._leggi_coda()

    assert set(coda) == {"2026-09-12", "2026-09-13"}


async def test_coda_legacy_dict_di_tentativi_migra_a_data(hass, make_pcf_coordinator):
    """Formato vecchio {data: n_tentativi}: al primo accesso ogni giorno prende
    'oggi' come data di primo inserimento (il timer riparte una volta sola)."""
    coordinator = make_pcf_coordinator(
        data={CONF_GIORNI_DA_RIPROVARE: {"2026-09-10": 4, "2026-09-11": 12}}
    )

    with freeze_time(ORA_SERA):
        coda = coordinator._leggi_coda()

    assert coda == {"2026-09-10": OGGI, "2026-09-11": OGGI}


async def test_coda_legacy_lista_migra_a_data(hass, make_pcf_coordinator):
    """Formato piu' vecchio ancora (lista di date): stessa migrazione a 'oggi'."""
    coordinator = make_pcf_coordinator(
        data={CONF_GIORNI_DA_RIPROVARE: ["2026-09-10", "2026-09-11"]}
    )

    with freeze_time(ORA_SERA):
        coda = coordinator._leggi_coda()

    assert coda == {"2026-09-10": OGGI, "2026-09-11": OGGI}
