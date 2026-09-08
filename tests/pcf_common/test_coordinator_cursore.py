"""Test end-to-end del PcfCoordinator nel modello a MESE CHIUSO.

Usano l'harness in conftest.py (FakePcfApi + statistiche/parsing patchati) e
freezegun per fissare la data. Verificano il comportamento osservabile:
quali richieste partono, come avanzano i cursori 'mese_da_importare' per
POD, cosa succede quando un mese non è (ancora) disponibile.
"""
from __future__ import annotations

from datetime import date

import pytest
from freezegun import freeze_time

from custom_components.contatore_letture.distributors.pcf_common.api import PcfApiError
from custom_components.contatore_letture.distributors.pcf_common.const import (
    CONF_MESE_DA_IMPORTARE,
    CONF_PENDING_DATA_A,
    CONF_PENDING_DATA_DA,
    CONF_PENDING_IS_BACKFILL,
    CONF_PENDING_TICKET,
    FASE_AUTOMATICA,
)

from .conftest import DF_TEST, POD_TEST, curva

OGGI = "2026-09-15 12:00:00"  # ora qualunque: non c'è più un vincolo orario
MESE_CHIUSO = "2026-08"
DA_AGO, A_AGO = date(2026, 8, 1), date(2026, 8, 31)

POD_A = POD_TEST
POD_B = "IT001E00000002"

pytestmark = pytest.mark.usefixtures("pcf_io")


@pytest.fixture(autouse=True)
async def _fuso_utc(hass):
    """Ancora il fuso di hass a UTC così dt_util.now().date() combacia con
    la data fissata da freeze_time."""
    await hass.config.async_set_time_zone("UTC")


async def _aggiorna(hass, coordinator):
    """Un ciclo del coordinator + attesa del task di polling in background."""
    await coordinator.async_refresh()
    await hass.async_block_till_done()


def _cursori(coordinator) -> dict:
    return dict(coordinator._entry.data.get(CONF_MESE_DA_IMPORTARE) or {})


async def test_primo_avvio_inizializza_il_cursore_al_mese_corrente(
    hass, make_pcf_coordinator
):
    """Senza cursore salvato: viene inizializzato al mese CORRENTE (non
    chiuso), quindi nessuna richiesta parte. Nessun backfill automatico."""
    coordinator = make_pcf_coordinator()

    with freeze_time(OGGI):
        await _aggiorna(hass, coordinator)

    assert coordinator.api.request_export.await_count == 0
    assert _cursori(coordinator) == {POD_TEST: "2026-09"}


async def test_cursore_su_mese_chiuso_chiede_l_intero_mese_e_avanza(
    hass, make_pcf_coordinator, pcf_io
):
    """Cursore su un mese già chiuso: il coordinator chiede quel mese intero
    (primo-ultimo giorno), e a import riuscito avanza il cursore al mese
    successivo e ripulisce il ticket pendente."""
    pcf_io.parse_zip.return_value = curva(date(2026, 8, 1), date(2026, 8, 15), date(2026, 8, 31))
    coordinator = make_pcf_coordinator(data={CONF_MESE_DA_IMPORTARE: {POD_TEST: MESE_CHIUSO}})

    with freeze_time(OGGI):
        await _aggiorna(hass, coordinator)

    assert coordinator.api.request_export.await_count == 1
    da, a = coordinator.api.request_export.await_args.args[:2]
    assert (da, a) == (DA_AGO, A_AGO)
    assert _cursori(coordinator) == {POD_TEST: "2026-09"}
    assert CONF_PENDING_TICKET not in coordinator._entry.data


async def test_avanza_solo_i_pod_presenti_nel_file(hass, make_pcf_coordinator, pcf_io):
    """Una sola requestExport per il gruppo di POD con lo stesso cursore; ma
    solo i POD per cui il file contiene dati avanzano, gli altri restano
    fermi e vengono riprovati al ciclo dopo."""
    pods = [{"pod": POD_A, "df": DF_TEST}, {"pod": POD_B, "df": DF_TEST}]
    pcf_io.parse_zip.return_value = curva(date(2026, 8, 10), pod=POD_A)  # solo A
    coordinator = make_pcf_coordinator(
        pods=pods,
        data={CONF_MESE_DA_IMPORTARE: {POD_A: MESE_CHIUSO, POD_B: MESE_CHIUSO}},
    )

    with freeze_time(OGGI):
        await _aggiorna(hass, coordinator)

    assert coordinator.api.request_export.await_count == 1
    pods_richiesti = {p["pod"] for p in coordinator.api.request_export.await_args.args[2]}
    assert pods_richiesti == {POD_A, POD_B}
    assert _cursori(coordinator) == {POD_A: "2026-09", POD_B: "2026-08"}


async def test_file_vuoto_lascia_il_cursore_fermo_e_riprova(
    hass, make_pcf_coordinator, pcf_io
):
    """Se il mese non è ancora disponibile (file senza dati) il cursore
    resta dov'è: nessuna coda, il ciclo successivo lo richiede di nuovo."""
    pcf_io.parse_zip.return_value = {}
    coordinator = make_pcf_coordinator(data={CONF_MESE_DA_IMPORTARE: {POD_TEST: MESE_CHIUSO}})

    with freeze_time(OGGI):
        await _aggiorna(hass, coordinator)
        assert _cursori(coordinator) == {POD_TEST: MESE_CHIUSO}
        assert coordinator.last_update_success is True
        assert CONF_PENDING_TICKET not in coordinator._entry.data

        await _aggiorna(hass, coordinator)

    assert coordinator.api.request_export.await_count == 2
    assert _cursori(coordinator) == {POD_TEST: MESE_CHIUSO}


async def test_nessun_mese_chiuso_non_chiede_nulla(hass, make_pcf_coordinator):
    """Cursore già al mese corrente: si aspetta che si chiuda, nessuna
    richiesta."""
    coordinator = make_pcf_coordinator(data={CONF_MESE_DA_IMPORTARE: {POD_TEST: "2026-09"}})

    with freeze_time(OGGI):
        await _aggiorna(hass, coordinator)

    assert coordinator.api.request_export.await_count == 0
    assert _cursori(coordinator) == {POD_TEST: "2026-09"}


async def test_ticket_in_sospeso_ripreso_senza_nuovo_export(
    hass, make_pcf_coordinator, pcf_io
):
    """Un CONF_PENDING_TICKET salvato da un ciclo precedente viene ripreso
    con requestResult: niente nuova requestExport. Essendo fase automatica e
    con dati nel file, il cursore del POD avanza."""
    pcf_io.parse_zip.return_value = curva(date(2026, 8, 5))
    coordinator = make_pcf_coordinator(
        data={
            CONF_MESE_DA_IMPORTARE: {POD_TEST: MESE_CHIUSO},
            CONF_PENDING_TICKET: "T-OLD",
            CONF_PENDING_DATA_DA: DA_AGO.isoformat(),
            CONF_PENDING_DATA_A: A_AGO.isoformat(),
            CONF_PENDING_IS_BACKFILL: FASE_AUTOMATICA,
        }
    )

    with freeze_time(OGGI):
        await _aggiorna(hass, coordinator)

    assert coordinator.api.request_export.await_count == 0
    assert coordinator.api.request_result.await_args.args[0] == "T-OLD"
    assert CONF_PENDING_TICKET not in coordinator._entry.data
    assert _cursori(coordinator) == {POD_TEST: "2026-09"}


async def test_export_rifiutata_segnala_fallimento_e_lascia_il_cursore(
    hass, make_pcf_coordinator
):
    """requestExport rifiutata: l'aggiornamento risulta fallito e il cursore
    resta dov'è (verrà richiesto di nuovo al ciclo successivo)."""
    coordinator = make_pcf_coordinator(data={CONF_MESE_DA_IMPORTARE: {POD_TEST: MESE_CHIUSO}})
    coordinator.api.request_export.side_effect = PcfApiError("richiesta rifiutata")

    with freeze_time(OGGI):
        await _aggiorna(hass, coordinator)

    assert coordinator.last_update_success is False
    assert _cursori(coordinator) == {POD_TEST: MESE_CHIUSO}


async def test_mese_gia_coperto_dalle_statistiche_avanza_senza_richiesta(
    hass, make_pcf_coordinator, pcf_io
):
    """Se le external statistics coprono già il mese del cursore (cursore
    perso, import fatto a mano), il ciclo avanza il cursore senza rifare
    requestExport."""
    pcf_io.ultima_data.return_value = date(2026, 8, 31)
    coordinator = make_pcf_coordinator(data={CONF_MESE_DA_IMPORTARE: {POD_TEST: MESE_CHIUSO}})

    with freeze_time(OGGI):
        await _aggiorna(hass, coordinator)

    assert coordinator.api.request_export.await_count == 0
    assert _cursori(coordinator) == {POD_TEST: "2026-09"}


async def test_recupera_storico_non_tocca_i_cursori(hass, make_pcf_coordinator, pcf_io):
    """L'azione recupera_storico è un percorso indipendente: fa una sola
    requestExport per il periodo e NON muove i cursori dell'automatico."""
    pcf_io.parse_zip.return_value = curva(date(2026, 4, 15))
    coordinator = make_pcf_coordinator(data={CONF_MESE_DA_IMPORTARE: {POD_TEST: MESE_CHIUSO}})

    with freeze_time(OGGI):
        await coordinator.async_recupera_storico(date(2026, 3, 1), date(2026, 4, 30))
        await hass.async_block_till_done()

    da, a = coordinator.api.request_export.await_args.args[:2]
    assert (da, a) == (date(2026, 3, 1), date(2026, 4, 30))
    assert _cursori(coordinator) == {POD_TEST: MESE_CHIUSO}


async def test_recupera_storico_rifiuta_il_mese_corrente(hass, make_pcf_coordinator):
    """Le CURVE si fermano al mese solare concluso: una data di fine nel mese
    corrente viene rifiutata subito."""
    from homeassistant.exceptions import ServiceValidationError

    coordinator = make_pcf_coordinator()
    with freeze_time(OGGI), pytest.raises(ServiceValidationError, match="mese solare concluso"):
        await coordinator.async_recupera_storico(date(2026, 8, 1), date(2026, 9, 10))


async def test_recupera_storico_rifiuta_oltre_cinque_anni(hass, make_pcf_coordinator):
    from homeassistant.exceptions import ServiceValidationError

    coordinator = make_pcf_coordinator()
    with freeze_time(OGGI), pytest.raises(ServiceValidationError, match="ultimi 5 anni"):
        await coordinator.async_recupera_storico(date(2020, 1, 1), date(2020, 2, 1))
