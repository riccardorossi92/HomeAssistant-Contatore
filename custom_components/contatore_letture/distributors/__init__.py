"""Registry centrale dei distributori supportati da contatore_letture.

Duereti e Unareti condividono la stessa libreria (pcf_common) ma restano
moduli distinti (distributors/duereti.py, distributors/unareti.py): se
domani uno dei due diverge, si cambia solo il suo modulo. E-Distribuzione
e' un pacchetto a se' (protocollo OAuth2+PKCE/OTP via Salesforce,
completamente diverso da PCF) - vedi distributors/edistribuzione/. Areti
e' un altro pacchetto a se' ancora (sessione a cookie su un portale
Salesforce diverso, nessun OTP, cursore mensile invece di coda
giornaliera) - vedi distributors/areti/.
"""
from __future__ import annotations

from . import areti, duereti, edistribuzione, unareti

# "kind" distingue il tipo di flow che il distributore usa nel config flow:
# "pcf" ha credenziali client_id/secret_id + lista POD (Duereti/Unareti,
# stesso schema condiviso), "edistribuzione" ha login email/password + OTP
# + selezione POD tra quelli dell'account, "areti" ha login email/password
# (nessun OTP osservato) + POD inseriti a mano (nessun elenco "tutti i POD
# dell'account" verificato, a differenza di edistribuzione).
DISTRIBUTOR_REGISTRY: dict[str, dict] = {
    "duereti": {
        "display_name": duereti.DISPLAY_NAME,
        "piva": duereti.PIVA,
        "kind": "pcf",
        "module": duereti,
        "required_info": duereti.REQUIRED_INFO,
    },
    "unareti": {
        "display_name": unareti.DISPLAY_NAME,
        "piva": unareti.PIVA,
        "kind": "pcf",
        "module": unareti,
        "required_info": unareti.REQUIRED_INFO,
    },
    "edistribuzione": {
        "display_name": edistribuzione.DISPLAY_NAME,
        "piva": edistribuzione.PIVA,
        "kind": "edistribuzione",
        "module": edistribuzione,
        "required_info": [
            "Email e password dell'area clienti E-Distribuzione",
            "Codice OTP (ricevuto via email o SMS al momento dell'accesso)",
        ],
    },
    "areti": {
        "display_name": areti.DISPLAY_NAME,
        "piva": areti.PIVA,
        "kind": "areti",
        "module": areti,
        "required_info": [
            "Email e password dell'area clienti Areti (areariservataclienti.areti.it)",
            "Codice POD per ciascun punto di prelievo",
        ],
    },
}

# Mappa inversa P.IVA -> chiave interna, usata dal config_flow per
# interpretare l'esito della query ARERA.
PIVA_TO_KEY: dict[str, str] = {
    info["piva"]: key for key, info in DISTRIBUTOR_REGISTRY.items() if info["piva"]
}
