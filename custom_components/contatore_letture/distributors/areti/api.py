"""Client per gli endpoint Aura del portale Areti.

Tutti via `POST /portaleareti/s/sfsites/aura`, descriptor
`aura://ApexActionController/ACTION$execute`, con `classname`/`method`
diversi - catena completa e struttura delle risposte documentate in
documentation/areti-protocol.md.

Richiede una sessione già autenticata (auth.py) e il suo AretiAuraContext
(fwuid/loaded_app_id/token): questo modulo non fa login, si limita a
comporre ed eseguire le chiamate.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

from .auth import AretiAuraContext
from .const import AURA_URL, BASE_URL, HOME_URL

_LOGGER = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


class AretiApiError(Exception):
    """Chiamata Aura fallita (trasporto, o state != SUCCESS lato server)."""


class AretiApiClient:
    def __init__(self, session: aiohttp.ClientSession, contesto: AretiAuraContext) -> None:
        self._session = session
        self._contesto = contesto
        self._contatore_r = 0

    async def _chiama_apex(
        self, classname: str, method: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Una chiamata aura://ApexActionController/ACTION$execute.

        Ritorna direttamente il valore di ritorno del metodo Apex:
        l'involucro Salesforce ('{"returnValue": <valore vero>,
        "cacheable": ...}') viene tolto qui, il chiamante non deve saperne
        nulla."""
        self._contatore_r += 1

        action_params: dict[str, Any] = {
            "namespace": "",
            "classname": classname,
            "method": method,
            "cacheable": False,
            "isContinuation": False,
        }
        if params is not None:
            action_params["params"] = params

        message = json.dumps({
            "actions": [{
                "id": f"{self._contatore_r};a",
                "descriptor": "aura://ApexActionController/ACTION$execute",
                "callingDescriptor": "UNKNOWN",
                "params": action_params,
            }]
        })
        aura_context = json.dumps({
            "mode": "PROD",
            "fwuid": self._contesto.fwuid,
            "app": "siteforce:communityApp",
            "loaded": {
                "APPLICATION@markup://siteforce:communityApp": self._contesto.loaded_app_id
            },
            "dn": [],
            "globals": {},
            "uad": True,
        })

        headers = {
            "User-Agent": _USER_AGENT,
            "Referer": HOME_URL,
            "Origin": BASE_URL,
        }

        try:
            async with self._session.post(
                AURA_URL,
                params={"r": self._contatore_r, "aura.ApexAction.execute": 1},
                data={
                    "message": message,
                    "aura.context": aura_context,
                    "aura.pageURI": "/portaleareti/s/",
                    "aura.token": self._contesto.token,
                },
                headers=headers,
            ) as resp:
                resp.raise_for_status()
                corpo = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise AretiApiError(f"Errore di trasporto chiamando {classname}.{method}: {err}") from err

        try:
            azione = corpo["actions"][0]
        except (KeyError, IndexError, TypeError) as err:
            raise AretiApiError(
                f"Risposta inattesa da {classname}.{method} (struttura 'actions' mancante): {corpo!r}"
            ) from err

        if azione.get("state") != "SUCCESS":
            raise AretiApiError(f"{classname}.{method} fallita: {azione.get('error')}")

        try:
            return azione["returnValue"]["returnValue"]
        except (KeyError, TypeError) as err:
            raise AretiApiError(
                f"Risposta inattesa da {classname}.{method} (returnValue mancante): {azione!r}"
            ) from err

    async def async_get_configurations(self, pod: str) -> dict[str, Any]:
        """Configurazione di misura del POD: codiceBP, codiceFiscale,
        is2G, unitOfMeasureMapping, energyOptions, mappingDatiMisura.

        codiceBP e codiceFiscale vanno passati alle chiamate successive
        (async_get_misurazioni) - non sono il POD né qualcosa che l'utente
        digita, li risolve questa chiamata."""
        risultato = await self._chiama_apex(
            "ARIA_DatiDiMisuraController", "getConfigurations", {"podName": pod}
        )
        if not risultato or not risultato.get("codiceBP"):
            raise AretiApiError(
                f"Nessun 'codiceBP' nella risposta per il POD {pod!r}: probabile "
                "che il POD non sia associato a questo account, o il formato non "
                "sia corretto (atteso IT + 2 cifre + lettera + 10 cifre, es. IT001E12345678)."
            )
        return risultato

    async def async_get_misurazioni(
        self,
        pod: str,
        codice_bp: str,
        codice_fiscale: str,
        mese_anno: str,
        componente_energia: str = "EA",
    ) -> dict[str, Any] | None:
        """Le misure vere per un mese (formato mese_anno: 'MMYYYY').

        Ritorna il primo elemento di misureByBP già deserializzato (con
        'esitoPosizioneBP' - letture cumulative a fine mese, NON il
        consumo del mese, vedi areti-protocol.md - e 'elementiCurve'/
        'elementiAggregati' - il consumo vero, a 15 minuti e giornaliero),
        o None se il portale non ha ancora nulla per quel mese (esito
        negativo o lista vuota): NON è un errore, è lo stato normale per
        un mese non ancora chiuso (vedi "Disponibilità" in
        areti-protocol.md)."""
        input_params_json = json.dumps({
            "useMock": False,
            "meseAnno": mese_anno,
            "codiceBP": codice_bp,
            "codiceFiscale": codice_fiscale,
            "pod": pod,
            "componenteEnergia": componente_energia,
        })
        misure_raw = await self._chiama_apex(
            "ARIA_DatiMisuraGetMisurazioni_WS",
            "getMisurazioni",
            {"inputParamsJson": input_params_json},
        )
        if not misure_raw or not misure_raw.get("esito"):
            _LOGGER.debug(
                "Nessun dato per POD %s, mese %s: %s",
                pod,
                mese_anno,
                misure_raw.get("errorMessage") if misure_raw else "(risposta vuota)",
            )
            return None

        misure_by_bp_raw = misure_raw.get("misureByBP")
        if not misure_by_bp_raw:
            return None
        try:
            misure = json.loads(misure_by_bp_raw)
        except (TypeError, ValueError) as err:
            raise AretiApiError(
                f"'misureByBP' non è JSON valido per POD {pod}, mese {mese_anno}: {err}"
            ) from err

        if not misure:
            return None
        return misure[0]
