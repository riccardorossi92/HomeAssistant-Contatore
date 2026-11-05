"""Client per il portale Edyna (ASP.NET WebForms, navigazione a postback).

Non esistono endpoint JSON: ogni passo è un POST a Single.tws con tutto il
form della pagina corrente (viewstate compreso) e __EVENTTARGET che indica
il link premuto. La risposta è la pagina intera, con tutti i tab aperti
(Utenze, Curve, Curve orarie): per questo il client tiene la pagina corrente
e cerca lì il link del passo successivo.

Catena per leggere un mese (documentation/protocols/edyna-protocol.md):

    Menu "Utenze"            -> griglia Utenze (un link "Curve" per riga)
    "Curve" della riga       -> tab Curve: selettore Anno + 12 mesi
    Selettore Anno (se serve) -> postback sul select
    Link del mese            -> tab Curve orarie: una riga per giorno, 25 colonne

Verificato su una cattura reale dell'intera catena (nome utente, menu,
Curve, cambio anno, mese). Due particolarità del portale:

- è lentissimo: i postback che caricano le curve (apertura del tab Curve,
  cambio anno) hanno impiegato fino a 104 secondi, il mese 40; il timeout
  è quindi largo;
- la prima richiesta di Curve o di un nuovo anno risponde con la tabella
  dei mesi vuota ("Nessun risultato"); ripetendo lo stesso postback la
  tabella arriva piena. Il client lo ripete una volta.

Se una pagina intermedia non porta il contenuto atteso si prova anche il
secondo postback del portale (body_ctl00_SecondPostback, quello che segue
il login e il menu), poi si solleva EdynaApiError con un messaggio che dice
a quale passo ci si è fermati.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

import aiohttp
from bs4 import BeautifulSoup

from .const import (
    HEADERS_BROWSER,
    ID_ANNO,
    ID_CAMPO_RICERCA_UTENZE,
    ID_GRIGLIA_ORARIA,
    ID_TABELLA_MESI,
    MENU_UTENZE,
    PORTALE_URL,
    RUOLO_PRELIEVO,
    SECOND_POSTBACK,
    SERVIZIO_ELETTRICO,
    SINGLE_URL,
)
from .parse import (
    EdynaParseError,
    Fornitura,
    anni_disponibili,
    anno_selezionato,
    campi_form,
    dati_postback,
    e_pagina_di_login,
    ha_elemento,
    kwh_mese,
    mese_della_curva,
    nome_campo_anno,
    parse_curva_oraria,
    parse_html,
    parse_utenze,
    pod_della_curva,
    tabella_mesi_vuota,
    target_menu,
    target_mese,
)

_LOGGER = logging.getLogger(__name__)

# Misurato su una cattura reale: fino a 104 s per una singola richiesta.
_TIMEOUT = aiohttp.ClientTimeout(total=300)
# Secondi postback tentati quando una pagina non porta ancora il contenuto.
_MAX_SECONDI_POSTBACK = 2
# Ripetizioni dello stesso postback quando la tabella dei mesi arriva vuota
# (la prima risposta è vuota, la seconda piena). Una sola: se anche la
# seconda è vuota l'anno non ha dati, ed ogni tentativo costa un minuto e più.
_RIPETIZIONI_SE_VUOTA = 1


class EdynaApiError(Exception):
    """Chiamata fallita (trasporto, HTTP inatteso, pagina inattesa)."""


class EdynaSessionScaduta(EdynaApiError):
    """Il portale ha risposto col modulo di login: sessione non valida."""


def _chiave_pod(pod: str) -> str:
    return pod.strip().upper()


class EdynaApiClient:
    def __init__(self, session: aiohttp.ClientSession, pagina_iniziale: BeautifulSoup) -> None:
        self._session = session
        self._pagina = pagina_iniziale
        self._forniture: list[Fornitura] | None = None
        self._pod_aperto: str | None = None

    # ------------------------------------------------------------------
    # Postback
    # ------------------------------------------------------------------

    async def _post(
        self, target: str, sovrascrivi: dict[str, str] | None = None
    ) -> BeautifulSoup:
        """Un postback a Single.tws; aggiorna e ritorna la pagina corrente."""
        loop = asyncio.get_running_loop()
        dati = await loop.run_in_executor(
            None, lambda: dati_postback(campi_form(self._pagina), target, "", sovrascrivi)
        )
        headers = {**HEADERS_BROWSER, "Origin": PORTALE_URL, "Referer": SINGLE_URL}
        try:
            async with self._session.post(
                SINGLE_URL, data=dati, headers=headers, timeout=_TIMEOUT
            ) as resp:
                if resp.status != 200:
                    raise EdynaApiError(f"POST Single.tws ({target}): HTTP {resp.status}")
                testo = await resp.text()
        except aiohttp.ClientError as err:
            raise EdynaApiError(f"Errore di trasporto nel postback {target}: {err}") from err
        except TimeoutError as err:
            raise EdynaApiError(f"Timeout nel postback {target}") from err
        soup = await loop.run_in_executor(None, parse_html, testo)
        if e_pagina_di_login(soup):
            raise EdynaSessionScaduta("Il portale Edyna ha chiesto di nuovo il login")
        self._pagina = soup
        return soup

    async def _vai_a(
        self,
        passo: str,
        target: str,
        atteso: Any,
        sovrascrivi: dict[str, str] | None = None,
        *,
        vuota: Any = None,
    ) -> BeautifulSoup:
        """Postback su 'target' e, finché la pagina non soddisfa 'atteso'
        (funzione soup -> bool), fino a _MAX_SECONDI_POSTBACK secondi
        postback. EdynaApiError col nome del 'passo' se non basta.

        vuota (funzione soup -> bool): la pagina è quella giusta ma i dati
        non sono ancora arrivati; si ripete lo stesso postback, al massimo
        _RIPETIZIONI_SE_VUOTA volte. Se resta vuota non è un errore: il
        chiamante decide (un anno senza dati è una situazione normale)."""
        soup = await self._post(target, sovrascrivi)
        for _ in range(_MAX_SECONDI_POSTBACK):
            if atteso(soup):
                break
            _LOGGER.debug("Edyna, passo '%s': contenuto assente, secondo postback", passo)
            soup = await self._post(SECOND_POSTBACK)
        if not atteso(soup):
            raise EdynaApiError(f"Edyna, passo '{passo}': la pagina non contiene quello che serve")
        if vuota is not None:
            for _ in range(_RIPETIZIONI_SE_VUOTA):
                if not vuota(soup):
                    break
                _LOGGER.debug("Edyna, passo '%s': tabella vuota, ripeto il postback", passo)
                soup = await self._post(target, sovrascrivi)
        return soup

    # ------------------------------------------------------------------
    # Utenze
    # ------------------------------------------------------------------

    async def _carica_forniture(self) -> list[Fornitura]:
        if self._forniture is not None:
            return self._forniture
        target = target_menu(self._pagina, MENU_UTENZE)
        if target is None:
            raise EdynaApiError("Voce di menu 'Utenze' non trovata nella pagina del portale")
        soup = await self._vai_a(
            "Utenze", target, lambda s: ha_elemento(s, ID_CAMPO_RICERCA_UTENZE)
        )
        try:
            self._forniture = parse_utenze(soup)
        except EdynaParseError as err:
            raise EdynaApiError(f"Griglia Utenze Edyna inattesa: {err}") from err
        self._pod_aperto = None
        return self._forniture

    async def async_get_pod_account(self) -> list[dict[str, str]]:
        """I POD elettrici di prelievo dell'account: [{"pod", "nome"}]. Il
        nome è la riga di stato del portale, per distinguerli nel selettore."""
        risultato: dict[str, dict[str, str]] = {}
        for f in await self._carica_forniture():
            if f.servizio != SERVIZIO_ELETTRICO or f.ruolo != RUOLO_PRELIEVO:
                continue
            risultato.setdefault(f.pod, {"pod": f.pod, "nome": f.stato})
        return list(risultato.values())

    def _fornitura(self, pod: str) -> Fornitura:
        for f in self._forniture or []:
            if (
                _chiave_pod(f.pod) == _chiave_pod(pod)
                and f.servizio == SERVIZIO_ELETTRICO
                and f.ruolo == RUOLO_PRELIEVO
            ):
                return f
        raise EdynaApiError(f"Il POD {pod} non risulta tra le forniture elettriche di prelievo")

    # ------------------------------------------------------------------
    # Curve
    # ------------------------------------------------------------------

    async def _apri_curve(self, f: Fornitura) -> BeautifulSoup:
        soup = await self._vai_a(
            "Curve",
            f.target_curve,
            lambda s: ha_elemento(s, ID_ANNO),
            vuota=tabella_mesi_vuota,
        )
        self._pod_aperto = _chiave_pod(f.pod)
        return soup

    async def async_get_curva_mese(
        self, pod: str, anno: int, mese: int
    ) -> dict[date, list[float | None]]:
        """Le righe orarie del mese: {giorno: [kWh ora 1 ... ora 25]}.
        Dizionario vuoto se il portale non ha dati per quell'anno o mese.
        Solleva EdynaApiError se la pagina non è quella attesa."""
        await self._carica_forniture()
        f = self._fornitura(pod)
        if self._pod_aperto != _chiave_pod(pod):
            await self._apri_curve(f)

        # Anno
        if anno_selezionato(self._pagina) != anno:
            if anno not in anni_disponibili(self._pagina):
                return {}
            nome = nome_campo_anno(self._pagina)
            if nome is None:
                raise EdynaApiError("Selettore dell'anno non trovato nel tab Curve")
            soup = await self._vai_a(
                f"Anno {anno}",
                nome,
                lambda s: anno_selezionato(s) == anno,
                sovrascrivi={nome: str(anno)},
                vuota=tabella_mesi_vuota,
            )
            if anno_selezionato(soup) != anno:
                raise EdynaApiError(f"Il portale non ha cambiato anno in {anno}")

        # Tabella dei mesi presente ma vuota anche dopo la ripetizione: l'anno
        # non ha dati. Non si riapre Curve (costerebbe un'altra manciata di
        # richieste da oltre un minuto) e non è un errore.
        if ha_elemento(self._pagina, ID_TABELLA_MESI) and tabella_mesi_vuota(self._pagina):
            return {}

        # Mese. Se il link non c'è più (il tab è stato rimpiazzato) si riapre
        # il tab Curve dalla griglia Utenze, una volta.
        if target_mese(self._pagina, mese) is None:
            await self._apri_curve(f)
            if anno_selezionato(self._pagina) != anno:
                return await self.async_get_curva_mese(pod, anno, mese)
        target = target_mese(self._pagina, mese)
        if target is None or kwh_mese(self._pagina, mese) is None:
            return {}

        soup = await self._vai_a(
            f"Mese {mese:02d}/{anno}", target, lambda s: ha_elemento(s, ID_GRIGLIA_ORARIA)
        )
        trovato = mese_della_curva(soup)
        if trovato != (anno, mese):
            raise EdynaApiError(
                f"Il portale ha restituito il mese {trovato} invece di {(anno, mese)}"
            )
        pod_pagina = pod_della_curva(soup)
        if pod_pagina is not None and _chiave_pod(pod_pagina) != _chiave_pod(pod):
            raise EdynaApiError(
                f"Il tab Curve orarie riguarda un altro POD ({pod_pagina}) invece di {pod}"
            )
        try:
            return parse_curva_oraria(soup)
        except EdynaParseError as err:
            raise EdynaApiError(f"Curve orarie Edyna illeggibili: {err}") from err
