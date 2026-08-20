"""Test sulla gestione degli errori delle API Duereti.

I payload usati qui NON sono inventati: sono le risposte reali osservate
chiamando le API, raccolte dopo che Duereti ha disattivato la policy del WAF
che le mascherava. Servono anche come documentazione eseguibile del
comportamento effettivo, che su diversi punti il manuale descrive in modo
vago o impreciso:

- il manuale dice che nel 409 il token esistente "viene restituito nel
  messaggio", ed è vero: sta dentro il testo, non in un campo;
- dice lo stesso per il ticket dell'elaborazione già in corso, ma lì sta in
  un campo JSON dedicato;
- non dice che un 400 su requestToken significa credenziali mancanti.

Come test_api.py, questi non richiedono Home Assistant installato.
"""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "custom_components" / "contatore_letture" / "distributors"))

from pcf_common.api import (  # noqa: E402
    PcfApiClient,
    PcfAuthError,
    PcfNotFoundError,
    PcfValidationError,
    descrivi_errore,
)

POD = [{"pod": "IT001E00000001", "df": "RSSMRA80A01F205X"}]

# --- risposte reali osservate --------------------------------------------

TOKEN_OK = (200, {"esito": 0, "token": "TOKEN_NUOVO"})
TOKEN_GIA_ATTIVO = (
    409,
    {"esito": 1, "message": "Esiste già un token registrato dall'utente, token: XMzKHaOOecofVWdx7gLIKA"},
)
CLIENTID_MANCANTE = (400, {"esito": 1, "message": "ClientID mancante"})
CREDENZIALI_ERRATE = (401, {"esito": 1, "message": "Dati non validi"})

EXPORT_DUPLICATO = (
    400,
    {
        "esito": 1,
        "message": "vi è già una richiesta di lettura con i medesimi dati in fase di elaborazione",
        "ticket": "PNbFjolxZVgoAnzA7v8bJw",
    },
)
DATE_INVERTITE = (
    400,
    {"esito": 1, "message": "il campo dataDa deve avere come valore una data precedente a quella del campo dataA"},
)
POD_NON_VALIDO = (400, {"esito": 1, "message": "Non ci sono POD/PDR validi"})

TOKEN_NON_VALIDO = (401, {"esito": 1, "message": "Token non valido"})
TICKET_NON_TROVATO = (404, {"esito": 1, "message": "Ticket non trovato"})
# Stesso status, significato diverso: il ticket è valido ma il periodo
# richiesto non contiene dati (es. date future).
NESSUN_DATO = (404, {"esito": 1, "message": "Nessun dato trovato"})
FILE_NON_PRONTO = (200, {"esito": 1, "message": "Il file non è ancora disponibile"})


# --- infrastruttura minima per simulare le risposte ----------------------


class _Risposta:
    def __init__(self, status, corpo):
        self.status = status
        self._corpo = corpo

    async def json(self, content_type=None):
        return self._corpo

    async def text(self):
        return json.dumps(self._corpo, ensure_ascii=False)

    @property
    def url(self):
        return "https://areaclienti.duereti.it/test"

    @property
    def headers(self):
        return {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _Sessione:
    """Restituisce una risposta fissa per requestToken e una per le altre.

    Se `altre` è una lista, ne consuma una per chiamata: serve a simulare
    sequenze come "prima rifiuta il token, poi restituisce il file".
    """

    def __init__(self, token=TOKEN_OK, altre=None):
        self.token = token
        self.altre = altre
        self.chiamate = 0

    def post(self, url, json=None, headers=None):
        if "requestToken" in url:
            return _Risposta(*self.token)
        self.chiamate += 1
        if isinstance(self.altre, list):
            indice = min(self.chiamate - 1, len(self.altre) - 1)
            return _Risposta(*self.altre[indice])
        return _Risposta(*self.altre)


def _client(sessione):
    return PcfApiClient(
        sessione, "client_id", "secret_id", "https://example.invalid/misure"
    )


# --- requestToken ---------------------------------------------------------


class TestRequestToken:
    @pytest.mark.asyncio
    async def test_token_gia_attivo_viene_riusato(self):
        """Il 409 contiene il token attivo nel testo del messaggio: va estratto
        e riusato, invece di attendere che scada."""
        sessione = _Sessione(token=TOKEN_GIA_ATTIVO)
        token = await _client(sessione)._get_token()
        assert token == "XMzKHaOOecofVWdx7gLIKA"

    @pytest.mark.asyncio
    async def test_token_riusato_resta_in_cache(self):
        """Una seconda richiesta non deve rifare la chiamata."""
        sessione = _Sessione(token=TOKEN_GIA_ATTIVO)
        client = _client(sessione)
        primo = await client._get_token()
        secondo = await client._get_token()
        assert primo == secondo

    @pytest.mark.asyncio
    async def test_credenziale_mancante_e_errore_di_autenticazione(self):
        """Un 400 su requestToken significa credenziali mancanti: deve far
        scattare la riautenticazione, non un generico errore di validazione."""
        with pytest.raises(PcfAuthError):
            await _client(_Sessione(token=CLIENTID_MANCANTE))._get_token()

    @pytest.mark.asyncio
    async def test_credenziali_errate(self):
        """Duereti non distingue quale credenziale sia sbagliata: risponde
        sempre 401 "Dati non validi", sia per clientId sia per secretId."""
        with pytest.raises(PcfAuthError):
            await _client(_Sessione(token=CREDENZIALI_ERRATE))._get_token()


# --- requestExport --------------------------------------------------------


class TestRequestExport:
    @pytest.mark.asyncio
    async def test_elaborazione_duplicata_riusa_il_ticket(self):
        """Il ticket dell'elaborazione già in corso sta in un campo dedicato
        della risposta, non nel messaggio: va riusato per non accodare un
        doppione."""
        sessione = _Sessione(altre=EXPORT_DUPLICATO)
        ticket = await _client(sessione).request_export(
            date(2026, 8, 1), date(2026, 8, 1), POD
        )
        assert ticket == "PNbFjolxZVgoAnzA7v8bJw"

    @pytest.mark.asyncio
    async def test_date_invertite_propaga_errore(self):
        """Un 400 di validazione vero non contiene alcun ticket e deve
        propagarsi, invece di essere scambiato per un duplicato."""
        with pytest.raises(PcfValidationError):
            await _client(_Sessione(altre=DATE_INVERTITE)).request_export(
                date(2026, 8, 5), date(2026, 8, 1), POD
            )

    @pytest.mark.asyncio
    async def test_pod_non_valido(self):
        """È il caso su cui si basa la verifica del POD in configurazione."""
        with pytest.raises(PcfValidationError, match="POD/PDR"):
            await _client(_Sessione(altre=POD_NON_VALIDO)).async_valida_pod(
                "IT999E99999999", "XXXXXX00X00X000X"
            )

    @pytest.mark.asyncio
    async def test_troppi_pod_in_una_richiesta(self):
        """Il limite di 200 POD è imposto dal client, senza chiamare l'API."""
        troppi = [{"pod": f"IT{i:012d}", "df": "X"} for i in range(201)]
        with pytest.raises(Exception, match="POD"):
            await _client(_Sessione()).request_export(
                date(2026, 8, 1), date(2026, 8, 1), troppi
            )


# --- requestResult --------------------------------------------------------


class TestRequestResult:
    @pytest.mark.asyncio
    async def test_ticket_non_trovato_riporta_il_ticket(self):
        """Il 404 è l'unico caso in cui il ticket viene scartato: il messaggio
        deve dire quale, non limitarsi a ripetere "non trovato"."""
        sessione = _Sessione(altre=TICKET_NON_TROVATO)
        with pytest.raises(PcfNotFoundError, match="PNbFjolxZVgoAnzA7v8bJw"):
            await _client(sessione).request_result("PNbFjolxZVgoAnzA7v8bJw")

    @pytest.mark.asyncio
    async def test_nessun_dato_non_dice_ticket_non_trovato(self):
        """Duereti usa lo stesso 404 anche quando il ticket è valido ma il
        periodo non contiene dati: il messaggio non deve far credere che il
        ticket sia inesistente."""
        sessione = _Sessione(altre=NESSUN_DATO)
        with pytest.raises(PcfNotFoundError) as errore:
            await _client(sessione).request_result("TICKET_VALIDO")
        assert "non trovato lato distributore" not in str(errore.value)
        assert "Nessun dato" in str(errore.value)

    @pytest.mark.asyncio
    async def test_token_rifiutato_due_volte_interrompe_subito(self):
        """Un 401 che persiste dopo il rinnovo del token non è una scadenza:
        continuare per tutti i tentativi (ore) terminerebbe con un fuorviante
        "file non disponibile"."""
        sessione = _Sessione(altre=TOKEN_NON_VALIDO)
        with pytest.raises(PcfAuthError):
            await _client(sessione).request_result("TICKET")
        assert sessione.chiamate == 2, "deve fermarsi al secondo rifiuto"

    @pytest.mark.asyncio
    async def test_token_scaduto_una_volta_viene_recuperato(self):
        """Il caso legittimo: il token era solo scaduto, il rinnovo funziona."""
        import base64

        file_ok = (200, {"esito": 0, "File": base64.b64encode(b"contenuto").decode()})
        sessione = _Sessione(altre=[TOKEN_NON_VALIDO, file_ok])
        risultato = await _client(sessione).request_result("TICKET")
        assert risultato == b"contenuto"


# --- messaggi d'errore ----------------------------------------------------


class TestCancellazione:
    """La cancellazione di un task non è un errore: succede a ogni reload
    della config entry e ogni volta che un'azione manuale ha la precedenza su
    un polling in corso. Va gestita esplicitamente perché asyncio.CancelledError
    non deriva da Exception e sfuggirebbe alla normale gestione degli errori,
    lasciando lo stato inconsistente."""

    def test_cancelled_error_non_e_una_exception(self):
        import asyncio

        assert not issubclass(asyncio.CancelledError, Exception)


class TestDescriviErrore:
    """La pagina HTML del WAF non deve finire nei messaggi mostrati
    all'utente: se ne estrae il solo Support ID, unica informazione utile per
    una segnalazione a Duereti."""

    PAGINA_WAF = (
        "Risposta non JSON (HTTP 200): <html><head><title>Request Rejected</title>"
        "</head><body>The requested URL was rejected. Please consult with your "
        "administrator.<br><br>Your support ID is: 11569801685490423380<br><br>"
        "<a href='javascript:history.back();'>[Go Back]</a></body></html>"
    )

    def test_estrae_il_support_id(self):
        risultato = descrivi_errore(Exception(self.PAGINA_WAF))
        assert "11569801685490423380" in risultato
        assert "<html>" not in risultato

    def test_altri_errori_restano_invariati(self):
        messaggio = "401 UNAUTHORIZED: Dati non validi"
        assert descrivi_errore(Exception(messaggio)) == messaggio
