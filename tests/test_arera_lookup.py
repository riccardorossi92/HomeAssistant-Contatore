"""Test del lookup ARERA: parsing dell'HTML della pagina "ricerca operatori"
e gestione degli errori di rete / struttura pagina cambiata.

_parse_result è la parte fragile (si rompe se ARERA cambia il markup): qui
è coperta con frammenti di HTML minimi. async_query_distributore usa una
sessione aiohttp finta.
"""
from __future__ import annotations

import pytest

from custom_components.contatore_letture import arera_lookup
from custom_components.contatore_letture.arera_lookup import (
    AreraLookupError,
    _parse_result,
    async_query_distributore,
)


def _item(ragione_sociale: str, piva: str) -> str:
    return (
        '<li class="ricercaOperatoreItem">'
        f"<h3>{ragione_sociale}</h3>"
        f'<a class="operatori-detail-link" href="/dettaglio?OperatorePartitaIva={piva}">dettaglio</a>'
        "</li>"
    )


# --- _parse_result --------------------------------------------------------

def test_nessun_item_ritorna_lista_vuota():
    assert _parse_result("<html><body><p>niente</p></body></html>") == []


def test_un_operatore():
    html = f"<ul>{_item('DUERETI S.P.A.', '13632560960')}</ul>"
    assert _parse_result(html) == [
        {"ragione_sociale": "DUERETI S.P.A.", "piva": "13632560960"}
    ]


def test_piu_operatori_in_ordine():
    html = f"<ul>{_item('A S.P.A.', '111')}{_item('B S.R.L.', '222')}</ul>"
    assert [o["piva"] for o in _parse_result(html)] == ["111", "222"]


def test_struttura_cambiata_senza_h3_o_link_solleva_errore():
    html = '<li class="ricercaOperatoreItem"><span>DUERETI</span></li>'
    with pytest.raises(AreraLookupError):
        _parse_result(html)


def test_struttura_cambiata_link_senza_partita_iva_solleva_errore():
    html = (
        '<li class="ricercaOperatoreItem"><h3>DUERETI</h3>'
        '<a class="operatori-detail-link" href="/dettaglio?Id=42">x</a></li>'
    )
    with pytest.raises(AreraLookupError):
        _parse_result(html)


# --- async_query_distributore -------------------------------------------------

class _FakeResp:
    def __init__(self, text: str = "", status: int = 200) -> None:
        self._text = text
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    async def text(self):
        return self._text


class _FakeSession:
    def __init__(self, resp=None, *, get_raises: Exception | None = None) -> None:
        self._resp = resp
        self._get_raises = get_raises

    def get(self, *args, **kwargs):
        if self._get_raises is not None:
            raise self._get_raises
        return self._resp


async def test_query_restituisce_gli_operatori_parsati(hass, monkeypatch):
    html = f"<ul>{_item('UNARETI S.P.A.', '12883450152')}</ul>"
    monkeypatch.setattr(
        arera_lookup, "async_get_clientsession", lambda _hass: _FakeSession(_FakeResp(html))
    )
    operatori = await async_query_distributore(hass, "03", "015", "015242")
    assert operatori == [{"ragione_sociale": "UNARETI S.P.A.", "piva": "12883450152"}]


async def test_query_incapsula_gli_errori_di_rete(hass, monkeypatch):
    monkeypatch.setattr(
        arera_lookup,
        "async_get_clientsession",
        lambda _hass: _FakeSession(get_raises=OSError("connessione rifiutata")),
    )
    with pytest.raises(AreraLookupError):
        await async_query_distributore(hass, "03", "015", "015242")
