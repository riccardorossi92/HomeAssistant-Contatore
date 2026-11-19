"""Scrittura delle statistiche orarie con un recorder vero (database in
memoria).

La serie salvata deve essere sempre esattamente quella del ricalcolo da zero
(com'era fino alla 0.17); cambia solo quante righe si riscrivono. Una serie
incoerente, come quelle lasciate dall'issue #10, si ripara al primo import
riscrivendola per intero."""
from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.contatore_letture import statistics_common as sc
from custom_components.contatore_letture.const import DOMAIN
from custom_components.contatore_letture.distributors.ireti import statistics as ireti

POD = "IT001E00000077"
STATISTIC_ID = "contatore_letture:it001e00000077_energia"


@pytest.fixture(autouse=True)
def _fuso_roma():
    originale = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Rome"))
    yield
    dt_util.set_default_time_zone(originale)


def _ora(anno: int, mese: int, giorno: int, ora: int) -> datetime:
    return datetime(anno, mese, giorno, ora, tzinfo=UTC)


async def _scrivi(hass, ore: dict[datetime, float], statistic_id: str = STATISTIC_ID):
    """Scrive con la funzione dell'integrazione e ritorna (data, righe passate
    al recorder in questa scrittura)."""
    with patch.object(
        sc, "async_add_external_statistics", wraps=sc.async_add_external_statistics
    ) as spia:
        data = await sc.async_scrivi_serie_oraria(hass, POD, statistic_id, "test", ore)
    await async_wait_recording_done(hass)
    righe_scritte = spia.call_args.args[2] if spia.call_args else []
    return data, righe_scritte


async def _serie_salvata(hass, statistic_id: str = STATISTIC_ID):
    await async_wait_recording_done(hass)
    risultato = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utc_from_timestamp(0),
        None,
        {statistic_id},
        "hour",
        None,
        {"state", "sum"},
    )
    return [
        (dt_util.utc_from_timestamp(r["start"]), r["state"], r["sum"])
        for r in risultato.get(statistic_id, [])
    ]


def _ricalcolo_da_zero(ore: dict[datetime, float]):
    """La serie che dava la 0.17: tutte le somme ricalcolate da zero."""
    somma = 0.0
    righe = []
    for inizio in sorted(ore):
        somma += ore[inizio]
        righe.append((inizio, ore[inizio], somma))
    return righe


def _uguali(salvata, attesa) -> None:
    assert [r[0] for r in salvata] == [r[0] for r in attesa]
    assert [r[1] for r in salvata] == pytest.approx([r[1] for r in attesa], abs=1e-9)
    assert [r[2] for r in salvata] == pytest.approx([r[2] for r in attesa], abs=1e-9)


async def _scrivi_serie_grezza(hass, righe: list[dict]) -> None:
    """Scrive righe {start, state, sum} cosi' come sono, anche incoerenti:
    serve a riprodurre serie lasciate da versioni precedenti."""
    async_add_external_statistics(
        hass,
        {
            "has_mean": False,
            "mean_type": StatisticMeanType.NONE,
            "has_sum": True,
            "name": "test",
            "source": DOMAIN,
            "statistic_id": STATISTIC_ID,
            "unit_of_measurement": "kWh",
            "unit_class": "energy",
        },
        righe,
    )
    await async_wait_recording_done(hass)


# --- comportamento di base ----------------------------------------------------


async def test_prima_scrittura_e_accodamento(hass):
    iniziali = {_ora(2026, 11, 1, h): 0.5 + h / 10 for h in range(3)}
    data, scritte = await _scrivi(hass, iniziali)
    assert data == date(2026, 11, 1)
    assert len(scritte) == 3

    nuove = {_ora(2026, 11, 2, 0): 3.0, _ora(2026, 11, 2, 1): 1.25}
    data, scritte = await _scrivi(hass, nuove)

    assert data == date(2026, 11, 2)
    assert [r["start"] for r in scritte] == sorted(nuove)  # le ore vecchie non si toccano
    _uguali(await _serie_salvata(hass), _ricalcolo_da_zero({**iniziali, **nuove}))


async def test_rettifica_in_mezzo_riscrive_solo_da_li_in_poi(hass):
    iniziali = {_ora(2026, 11, 1, h): float(h + 1) for h in range(6)}
    await _scrivi(hass, iniziali)

    rettifica = {_ora(2026, 11, 1, 3): 10.0}
    _data, scritte = await _scrivi(hass, rettifica)

    assert [r["start"] for r in scritte] == [_ora(2026, 11, 1, h) for h in range(3, 6)]
    _uguali(await _serie_salvata(hass), _ricalcolo_da_zero({**iniziali, **rettifica}))


async def test_ore_prima_dell_inizio_spostano_tutta_la_serie(hass):
    iniziali = {_ora(2026, 11, 10, 0): 1.0, _ora(2026, 11, 10, 1): 2.0}
    await _scrivi(hass, iniziali)

    storico = {_ora(2026, 11, 5, 12): 4.0}
    _data, scritte = await _scrivi(hass, storico)

    assert len(scritte) == 3
    _uguali(await _serie_salvata(hass), _ricalcolo_da_zero({**iniziali, **storico}))


async def test_nessuna_ora_nuova_riscrive_tutto_come_prima(hass):
    iniziali = {_ora(2026, 11, 1, 0): 1.0, _ora(2026, 11, 3, 5): 2.0}
    await _scrivi(hass, iniziali)

    data, scritte = await _scrivi(hass, {})

    assert data == date(2026, 11, 3)
    assert len(scritte) == 2
    _uguali(await _serie_salvata(hass), _ricalcolo_da_zero(iniziali))


async def test_nessuna_ora_nuova_su_serie_vuota(hass):
    data, scritte = await _scrivi(hass, {})
    assert data is None
    assert scritte == []


async def test_interruttore_spento_riscrive_sempre_tutto(hass, monkeypatch):
    iniziali = {_ora(2026, 11, 1, h): 1.0 for h in range(4)}
    await _scrivi(hass, iniziali)
    monkeypatch.setattr(sc, "RISCRITTURA_PARZIALE", False)

    nuove = {_ora(2026, 11, 2, 0): 2.0}
    _data, scritte = await _scrivi(hass, nuove)

    assert len(scritte) == 5
    _uguali(await _serie_salvata(hass), _ricalcolo_da_zero({**iniziali, **nuove}))


# --- issue #10 ------------------------------------------------------------------


def _giorno_ireti(giorno: str, valore: float) -> dict:
    return {
        "loadProfileDate": f"{giorno} 00:00:00 +0200",
        "serialNumber": "serial-fantasia",
        "timeType": "",
        "energyType": "A1",
        "measType": "0",
        "sampleValues": [valore] * 96,
    }


def _giorno_da_25_ore_ireti() -> list[dict]:
    """26/10/2025 come nella issue #10: due elementi A1 con la stessa data,
    uno da 12 campioni (00:00-02:59 CEST) e uno da 96 con i primi 8 a null
    (dalle 02:00 CET). Ogni ora trascorsa vale 0,1 kWh, quindi 2,5 kWh in
    tutto il giorno."""
    corto = [0.025] * 12
    lungo = [None] * 8 + [0.025] * 88
    return [
        {**_giorno_ireti("26/10/2025", 0.0), "sampleValues": corto},
        {**_giorno_ireti("26/10/2025", 0.0), "sampleValues": lungo},
    ]


def _ore_di_un_giorno_locale(giorno: date, kwh: float) -> dict[datetime, float]:
    inizio = dt_util.as_utc(dt_util.start_of_local_day(giorno))
    fine = dt_util.as_utc(dt_util.start_of_local_day(giorno + timedelta(days=1)))
    ore = {}
    istante = inizio
    while istante < fine:
        ore[istante] = kwh
        istante += timedelta(hours=1)
    return ore


def _serie_danneggiata(ore: dict[datetime, float], ripartenze: set[datetime]) -> list[dict]:
    """Come la lasciavano le versioni con l'issue #10: la somma riparte da
    zero all'inizio di alcuni blocchi del recupero storico."""
    righe = []
    somma = 0.0
    for inizio in sorted(ore):
        if inizio in ripartenze:
            somma = 0.0
        somma += ore[inizio]
        righe.append({"start": inizio, "state": ore[inizio], "sum": somma})
    return righe


@pytest.mark.parametrize(
    "ripartenza",
    [
        pytest.param(date(2025, 10, 23), id="pochi-giorni-prima"),
        pytest.param(date(2025, 3, 11), id="mesi-prima"),
    ],
)
async def test_issue_10_serie_danneggiata_e_giorno_da_25_ore(hass, ripartenza):
    """Serie lasciata dall'issue #10: somme che ripartono da zero a un confine
    di blocco, e il 26/10/2025 (25 ore) con le 00 e le 01 mancanti. Poi
    arriva il 26/10 giusto, come due elementi Ireti con la stessa data, nel
    giro automatico. Risultato: la serie viene riscritta per intero, come
    nella 0.17, ovunque fosse il danno."""
    vecchie: dict[datetime, float] = {}
    for giorno in [date(2025, 3, 10), date(2025, 3, 11), date(2025, 3, 12)]:
        vecchie.update(_ore_di_un_giorno_locale(giorno, 0.2))
    for numero in range(20, 28):
        vecchie.update(_ore_di_un_giorno_locale(date(2025, 10, numero), 0.3))
    # Il 26/10 importato male: mancano la prima e la seconda ora del giorno.
    mezzanotte_26 = dt_util.as_utc(dt_util.start_of_local_day(date(2025, 10, 26)))
    del vecchie[mezzanotte_26]
    del vecchie[mezzanotte_26 + timedelta(hours=1)]
    ripartenze = {dt_util.as_utc(dt_util.start_of_local_day(ripartenza))}
    await _scrivi_serie_grezza(hass, _serie_danneggiata(vecchie, ripartenze))

    with patch.object(
        sc, "async_add_external_statistics", wraps=sc.async_add_external_statistics
    ) as spia:
        data = await ireti.async_import_curva_giorni(hass, POD, _giorno_da_25_ore_ireti())
    await async_wait_recording_done(hass)

    assert data == date(2025, 10, 27)
    ore_26 = [mezzanotte_26 + timedelta(hours=n) for n in range(25)]
    attese = {**vecchie, **dict.fromkeys(ore_26, 0.1)}
    assert len(spia.call_args.args[2]) == len(attese)  # riscritta per intero
    salvata = await _serie_salvata(hass)
    _uguali(salvata, _ricalcolo_da_zero(attese))
    # Il 26/10 ha 25 ore e 2,5 kWh, e da nessuna parte la somma torna indietro.
    giorno_26 = [r for r in salvata if dt_util.as_local(r[0]).date() == date(2025, 10, 26)]
    assert len(giorno_26) == 25
    assert sum(r[1] for r in giorno_26) == pytest.approx(2.5)
    assert all(b[2] >= a[2] for a, b in zip(salvata, salvata[1:], strict=False))


async def test_issue_10_dopo_la_riparazione_si_torna_alla_scrittura_parziale(hass):
    vecchie = _ore_di_un_giorno_locale(date(2025, 10, 20), 0.3)
    vecchie.update(_ore_di_un_giorno_locale(date(2025, 10, 21), 0.3))
    ripartenze = {dt_util.as_utc(dt_util.start_of_local_day(date(2025, 10, 21)))}
    await _scrivi_serie_grezza(hass, _serie_danneggiata(vecchie, ripartenze))

    giorno_22 = _ore_di_un_giorno_locale(date(2025, 10, 22), 0.4)
    _data, scritte = await _scrivi(hass, giorno_22)
    assert len(scritte) == 72  # prima volta: riparata per intero

    giorno_23 = _ore_di_un_giorno_locale(date(2025, 10, 23), 0.5)
    _data, scritte = await _scrivi(hass, giorno_23)
    assert len(scritte) == 24  # serie ormai coerente: solo il giorno nuovo

    _uguali(
        await _serie_salvata(hass),
        _ricalcolo_da_zero({**vecchie, **giorno_22, **giorno_23}),
    )


# --- confronto con il ricalcolo totale -------------------------------------------


async def test_stesso_risultato_del_ricalcolo_totale_con_rettifiche_e_storico(
    hass, monkeypatch
):
    """Due POD ricevono gli stessi import, uno con la scrittura parziale e
    uno con l'interruttore spento (cioe' come nella 0.17). Gli import sono
    60, a caso ma ripetibili: giorni nuovi, rettifiche di ore gia'
    importate (anche in mezzo), recuperi storici prima dell'inizio e blocchi
    che si sovrappongono, sui giorni intorno ai due cambi d'ora (25 e 23
    ore). Dopo ogni import le due serie salvate devono coincidere."""
    parziale = "contatore_letture:pod_parziale_energia"
    totale = "contatore_letture:pod_totale_energia"
    caso = random.Random(20261119)
    giorni = [date(2025, 10, 20) + timedelta(days=n) for n in range(10)] + [
        date(2026, 3, 25) + timedelta(days=n) for n in range(10)
    ]
    tutte_le_ore = [ora for giorno in giorni for ora in _ore_di_un_giorno_locale(giorno, 0)]
    ultima = 24 * 3  # parte con qualche giorno gia' disponibile
    importate: set[int] = set()
    righe_parziale = righe_totale = 0

    for passo in range(60):
        scelta = caso.random()
        if scelta < 0.4 or not importate:
            # giorni nuovi in coda (anche piu' giorni insieme, come un arretrato)
            da = max(importate, default=ultima) + 1
            indici = range(da, min(da + 24 * caso.randint(1, 4), len(tutte_le_ore)))
        elif scelta < 0.7:
            # rettifica di qualche ora gia' importata, ovunque
            indici = caso.sample(sorted(importate), k=min(len(importate), caso.randint(1, 6)))
        elif scelta < 0.85:
            # recupero storico prima dell'inizio attuale
            fino = min(importate)
            indici = range(max(0, fino - 24 * caso.randint(1, 5)), fino)
        else:
            # blocco che si sovrappone a ore gia' importate e ne aggiunge
            centro = caso.choice(sorted(importate))
            indici = range(max(0, centro - 30), min(len(tutte_le_ore), centro + 30))
        indici = [i for i in indici if 0 <= i < len(tutte_le_ore)]
        if not indici:
            continue
        nuove = {tutte_le_ore[i]: round(caso.uniform(0, 3), 3) for i in indici}
        importate.update(indici)

        _d, scritte = await _scrivi(hass, nuove, parziale)
        righe_parziale += len(scritte)
        with monkeypatch.context() as m:
            m.setattr(sc, "RISCRITTURA_PARZIALE", False)
            _d, scritte = await _scrivi(hass, nuove, totale)
        righe_totale += len(scritte)

        a = await _serie_salvata(hass, parziale)
        b = await _serie_salvata(hass, totale)
        assert [r[0] for r in a] == [r[0] for r in b], passo
        assert [r[1] for r in a] == [r[1] for r in b], passo
        assert [r[2] for r in a] == pytest.approx([r[2] for r in b], abs=1e-9), passo

    # Stessa serie con meno righe scritte. Qui il risparmio e' moderato perche'
    # i recuperi storici e le rettifiche lontane fanno ripartire la scrittura
    # da molto indietro; nell'import giornaliero si scrive solo il giorno
    # nuovo (vedi test_prima_scrittura_e_accodamento).
    assert righe_parziale < righe_totale
