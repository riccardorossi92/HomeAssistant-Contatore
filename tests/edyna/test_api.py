"""Test di distributors/edyna/api.py: la catena di postback Utenze -> Curve ->
mese, su un portale finto che risponde con le pagine anonimizzate di
dati_reali.py in base al __EVENTTARGET. Nessuna chiamata di rete reale.

La catena è stata verificata anche su una cattura reale (05/11/2026, rigiocata
fuori dal repository perché pesa megabyte e contiene dati personali): il
portale finto ne riproduce la forma, compresa la prima risposta vuota di Curve
e del cambio anno (TestPortaleLento). I test verificano la logica del client,
non il portale vero."""
from __future__ import annotations

from datetime import date

import aiohttp
import pytest

from custom_components.contatore_letture.distributors.edyna import api as modulo
from custom_components.contatore_letture.distributors.edyna import parse as p
from custom_components.contatore_letture.distributors.edyna.const import SECOND_POSTBACK

from . import dati_reali as d

TARGET_UTENZE = "ctl00$body$ctl00$mMenu1$FirstLevelMenuRepeater$ctl01$lnkLevelMenu"
TARGET_CURVE_1 = "ctl00$body$ctl00$ctl00$tcListUtenze$TList$cUFListUtenze$gvUtenze$ctl02$btnCurve"
TARGET_CURVE_2 = TARGET_CURVE_1.replace("ctl02", "ctl05")
TARGET_ANNO = "ctl00$body$ctl00$ctl00$tcListUtenze$TCurve$cCurve$ddlAnno"


def _target_mese(mese: int) -> str:
    return (
        "ctl00$body$ctl00$ctl00$tcListUtenze$TCurve$cCurve$gvCurveAttiva$ctl02$btnCurve"
        f"{mese}"
    )


class _Risposta:
    def __init__(self, testo: str, status: int = 200):
        self.status = status
        self._testo = testo

    async def text(self):
        return self._testo

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class PortaleFinto:
    """Sessione che finge Single.tws: 'percorsi' mappa __EVENTTARGET -> pagina
    (HTML) o funzione dati -> HTML."""

    def __init__(self, percorsi: dict, errore: Exception | None = None):
        self.percorsi = percorsi
        self.errore = errore
        self.richieste: list[dict[str, str]] = []

    def post(self, url, *, data, **kw):
        if self.errore:
            raise self.errore
        campi = dict(data)
        self.richieste.append(campi)
        risposta = self.percorsi.get(campi["__EVENTTARGET"])
        if risposta is None:
            return _Risposta("errore", status=500)
        return _Risposta(risposta(campi) if callable(risposta) else risposta)

    @property
    def target(self) -> list[str]:
        return [r["__EVENTTARGET"] for r in self.richieste]


def _client(percorsi, errore=None, griglia=d.GRIGLIA_UTENZE):
    sessione = PortaleFinto(percorsi, errore)
    return modulo.EdynaApiClient(sessione, p.parse_html(d.pagina_home())), sessione


def _percorsi_base(**extra):
    percorsi = {
        TARGET_UTENZE: d.pagina_utenze(),
        TARGET_CURVE_1: d.pagina_curve(),
        _target_mese(10): d.pagina_curve_orarie(),
    }
    percorsi.update(extra)
    return percorsi


class TestPodDellAccount:
    async def test_un_pod(self):
        api, sessione = _client(_percorsi_base())
        assert await api.async_get_pod_account() == [{"pod": d.POD, "nome": "Attivo"}]
        assert sessione.target == [TARGET_UTENZE]

    async def test_solo_prelievo_elettrico(self):
        api, _ = _client(_percorsi_base(**{TARGET_UTENZE: d.pagina_utenze(d.GRIGLIA_UTENZE_MULTI)}))
        pods = [x["pod"] for x in await api.async_get_pod_account()]
        assert pods == [d.POD, d.POD_SECONDO]  # niente immissione, niente gas

    async def test_account_senza_pod(self):
        api, _ = _client(_percorsi_base(**{TARGET_UTENZE: d.pagina_utenze_vuota()}))
        assert await api.async_get_pod_account() == []

    async def test_le_forniture_si_leggono_una_volta(self):
        api, sessione = _client(_percorsi_base())
        await api.async_get_pod_account()
        await api.async_get_pod_account()
        assert sessione.target.count(TARGET_UTENZE) == 1

    async def test_secondo_postback_se_la_pagina_arriva_vuota(self):
        # Come su DEA: il menu risponde con la pagina senza contenuto, il
        # contenuto arriva col secondo postback.
        api, sessione = _client(
            _percorsi_base(**{TARGET_UTENZE: d.pagina_home(2), SECOND_POSTBACK: d.pagina_utenze()})
        )
        assert [x["pod"] for x in await api.async_get_pod_account()] == [d.POD]
        assert sessione.target == [TARGET_UTENZE, SECOND_POSTBACK]

    async def test_pagina_che_non_arriva_mai(self):
        api, sessione = _client(
            _percorsi_base(**{TARGET_UTENZE: d.pagina_home(2), SECOND_POSTBACK: d.pagina_home(3)})
        )
        with pytest.raises(modulo.EdynaApiError, match="Utenze"):
            await api.async_get_pod_account()
        assert sessione.target == [TARGET_UTENZE, SECOND_POSTBACK, SECOND_POSTBACK]

    async def test_senza_voce_di_menu(self):
        sessione = PortaleFinto({})
        api = modulo.EdynaApiClient(sessione, p.parse_html(d.pagina("<p>niente menu</p>")))
        with pytest.raises(modulo.EdynaApiError, match="Utenze"):
            await api.async_get_pod_account()


class TestCurvaDelMese:
    async def test_catena_completa(self):
        api, sessione = _client(_percorsi_base())
        curva = await api.async_get_curva_mese(d.POD, 2026, 10)

        assert sessione.target == [TARGET_UTENZE, TARGET_CURVE_1, _target_mese(10)]
        assert list(curva)[0] == date(2026, 10, 1) and len(curva) == 4
        assert len(curva[date(2026, 10, 25)]) == 25

    async def test_il_postback_riporta_il_viewstate_della_pagina_precedente(self):
        api, sessione = _client(_percorsi_base())
        await api.async_get_curva_mese(d.POD, 2026, 10)
        # Utenze (pagina home n.1) -> Curve (pagina utenze n.2) -> mese (pagina curve n.3)
        assert [r["__VIEWSTATE"] for r in sessione.richieste] == ["VS-1", "VS-2", "VS-3"]
        # Il selettore dell'anno viaggia col valore corrente.
        assert sessione.richieste[2][TARGET_ANNO] == "2026"

    async def test_secondo_mese_dello_stesso_pod_non_riapre_le_curve(self):
        api, sessione = _client(
            _percorsi_base(**{_target_mese(9): d.pagina_curve_orarie(mese="09/2026")})
        )
        await api.async_get_curva_mese(d.POD, 2026, 10)
        await api.async_get_curva_mese(d.POD, 2026, 9)
        assert sessione.target == [TARGET_UTENZE, TARGET_CURVE_1, _target_mese(10), _target_mese(9)]

    async def test_altro_anno_con_postback_sul_selettore(self):
        api, sessione = _client(
            _percorsi_base(
                **{
                    TARGET_ANNO: d.pagina_curve(selettore=d.selettore_anno(2025)),
                    _target_mese(10): lambda campi: d.pagina_curve_orarie(
                        mese="10/2025", selettore=d.selettore_anno(2025)
                    ),
                }
            )
        )
        await api.async_get_curva_mese(d.POD, 2025, 10)
        assert sessione.target == [TARGET_UTENZE, TARGET_CURVE_1, TARGET_ANNO, _target_mese(10)]
        assert sessione.richieste[2][TARGET_ANNO] == "2025"

    async def test_anno_non_disponibile(self):
        api, sessione = _client(_percorsi_base())
        assert await api.async_get_curva_mese(d.POD, 2010, 10) == {}
        assert sessione.target == [TARGET_UTENZE, TARGET_CURVE_1]

    async def test_il_portale_non_cambia_anno(self):
        api, _ = _client(
            _percorsi_base(**{TARGET_ANNO: d.pagina_curve(), SECOND_POSTBACK: d.pagina_curve()})
        )
        with pytest.raises(modulo.EdynaApiError, match="Anno 2025"):
            await api.async_get_curva_mese(d.POD, 2025, 10)

    async def test_mese_senza_valore_non_costa_richieste(self):
        api, sessione = _client(_percorsi_base())
        assert await api.async_get_curva_mese(d.POD, 2026, 11) == {}
        assert sessione.target == [TARGET_UTENZE, TARGET_CURVE_1]

    async def test_il_portale_restituisce_un_altro_mese(self):
        api, _ = _client(
            _percorsi_base(**{_target_mese(10): d.pagina_curve_orarie(mese="09/2026")})
        )
        with pytest.raises(modulo.EdynaApiError, match="mese"):
            await api.async_get_curva_mese(d.POD, 2026, 10)

    async def test_il_portale_restituisce_un_altro_pod(self):
        altra = d.INTESTAZIONE_TAB_CURVE_ORARIE.replace(d.POD, d.POD_SECONDO)
        api, _ = _client(
            _percorsi_base(**{_target_mese(10): d.pagina_curve_orarie(intestazione_orarie=altra)})
        )
        with pytest.raises(modulo.EdynaApiError, match="altro POD"):
            await api.async_get_curva_mese(d.POD, 2026, 10)

    async def test_pod_sconosciuto(self):
        api, _ = _client(_percorsi_base())
        with pytest.raises(modulo.EdynaApiError, match="non risulta"):
            await api.async_get_curva_mese("IT000E99999999", 2026, 10)

    async def test_pod_di_immissione_non_si_legge(self):
        api, _ = _client(_percorsi_base(**{TARGET_UTENZE: d.pagina_utenze(d.GRIGLIA_UTENZE_MULTI)}))
        with pytest.raises(modulo.EdynaApiError, match="non risulta"):
            await api.async_get_curva_mese(d.POD_IMMISSIONE, 2026, 10)

    async def test_secondo_pod_riapre_le_curve_dalla_sua_riga(self):
        api, sessione = _client(
            _percorsi_base(
                **{
                    TARGET_UTENZE: d.pagina_utenze(d.GRIGLIA_UTENZE_MULTI),
                    TARGET_CURVE_2: d.pagina_curve(griglia=d.GRIGLIA_UTENZE_MULTI),
                }
            )
        )
        await api.async_get_curva_mese(d.POD, 2026, 10)
        altra = d.INTESTAZIONE_TAB_CURVE_ORARIE.replace(d.POD, d.POD_SECONDO)
        sessione.percorsi[_target_mese(10)] = d.pagina_curve_orarie(intestazione_orarie=altra)
        await api.async_get_curva_mese(d.POD_SECONDO, 2026, 10)
        assert sessione.target == [
            TARGET_UTENZE, TARGET_CURVE_1, _target_mese(10), TARGET_CURVE_2, _target_mese(10),
        ]


class _Sequenza:
    """Risposta del portale finto che cambia a ogni richiesta: come Curve e il
    cambio anno, vuoti alla prima richiesta e pieni alla seconda."""

    def __init__(self, *pagine):
        self.pagine = list(pagine)
        self.chiamate = 0

    def __call__(self, campi):
        pagina = self.pagine[min(self.chiamate, len(self.pagine) - 1)]
        self.chiamate += 1
        return pagina


class TestPortaleLento:
    """Comportamento visto nella cattura del 05/11/2026: la prima richiesta di
    Curve (e di un anno nuovo) risponde con "Nessun risultato", la ripetizione
    dello stesso postback porta i dati; ogni richiesta può durare oltre 100 s."""

    def test_timeout_largo(self):
        assert modulo._TIMEOUT.total >= 180

    async def test_curve_vuote_alla_prima_richiesta_si_ripete_il_postback(self):
        curve = _Sequenza(d.pagina_curve(tabella=d.TABELLA_MESI_VUOTA), d.pagina_curve())
        api, sessione = _client(_percorsi_base(**{TARGET_CURVE_1: curve}))
        curva = await api.async_get_curva_mese(d.POD, 2026, 10)
        assert len(curva) == 4
        assert sessione.target == [TARGET_UTENZE, TARGET_CURVE_1, TARGET_CURVE_1, _target_mese(10)]

    async def test_anno_nuovo_vuoto_alla_prima_richiesta_si_ripete_il_postback(self):
        anno = _Sequenza(
            d.pagina_curve(selettore=d.selettore_anno(2025), tabella=d.TABELLA_MESI_VUOTA),
            d.pagina_curve(selettore=d.selettore_anno(2025)),
        )
        api, sessione = _client(
            _percorsi_base(
                **{
                    TARGET_ANNO: anno,
                    _target_mese(10): d.pagina_curve_orarie(
                        mese="10/2025", selettore=d.selettore_anno(2025)
                    ),
                }
            )
        )
        await api.async_get_curva_mese(d.POD, 2025, 10)
        assert sessione.target == [
            TARGET_UTENZE, TARGET_CURVE_1, TARGET_ANNO, TARGET_ANNO, _target_mese(10),
        ]
        assert sessione.richieste[3][TARGET_ANNO] == "2025"

    async def test_tabella_sempre_vuota_e_un_anno_senza_dati_non_un_errore(self):
        curve = _Sequenza(d.pagina_curve(tabella=d.TABELLA_MESI_VUOTA))
        api, sessione = _client(_percorsi_base(**{TARGET_CURVE_1: curve}))
        assert await api.async_get_curva_mese(d.POD, 2026, 10) == {}
        # Una sola ripetizione: ogni tentativo costa più di un minuto.
        assert sessione.target == [TARGET_UTENZE, TARGET_CURVE_1, TARGET_CURVE_1]

    async def test_curve_piene_non_si_ripetono(self):
        api, sessione = _client(_percorsi_base())
        await api.async_get_curva_mese(d.POD, 2026, 10)
        assert sessione.target.count(TARGET_CURVE_1) == 1


class TestErrori:
    async def test_sessione_scaduta(self):
        api, _ = _client(_percorsi_base(**{TARGET_CURVE_1: d.MODULO_LOGIN}))
        with pytest.raises(modulo.EdynaSessionScaduta):
            await api.async_get_curva_mese(d.POD, 2026, 10)

    async def test_http_500(self):
        api, _ = _client({})
        with pytest.raises(modulo.EdynaApiError, match="HTTP 500"):
            await api.async_get_pod_account()

    async def test_errore_di_rete(self):
        api, _ = _client({}, errore=aiohttp.ClientConnectionError("giù"))
        with pytest.raises(modulo.EdynaApiError, match="trasporto"):
            await api.async_get_pod_account()

    async def test_timeout(self):
        api, _ = _client({}, errore=TimeoutError())
        with pytest.raises(modulo.EdynaApiError, match="Timeout"):
            await api.async_get_pod_account()

    async def test_griglia_oraria_assente(self):
        # Il passo "Mese" fallisce con un messaggio che nomina il passo.
        api, _ = _client(
            _percorsi_base(**{_target_mese(10): d.pagina_curve(), SECOND_POSTBACK: d.pagina_curve()})
        )
        with pytest.raises(modulo.EdynaApiError, match=r"Mese 10/2026"):
            await api.async_get_curva_mese(d.POD, 2026, 10)
