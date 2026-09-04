"""Login Areti (areariservataclienti.areti.it) e contesto Aura.

Non è il login standard "Salesforce Community" (`POST .../s/login`): Areti
mette davanti al sito Lightning una vecchia pagina di login
Visualforce/JSF (`AretiLoginURL`). Tre passaggi, tutti verificati su
cattura reale il 04/09/2026 con credenziali vere - dettagli completi e
"perché" in documentation/areti-protocol.md, sezione "Login":

  1. GET AretiLoginURL -> HTML con 3 campi nascosti (ViewState/
     ViewStateVersion/ViewStateMAC) da rileggere ad ogni tentativo.
  2. POST AretiLoginURL?refURL=... con email/password + quei 3 campi ->
     header 'Location' con un URL contenente un ticket ('cshc=...').
     NESSUN passaggio OTP osservato in questa cattura.
  3. GET quell'URL (secur/frontdoor.jsp) -> imposta i cookie di sessione
     veri (sid, sid_Client, ...).

Da lì, GET della home (/s/) carica l'area riservata loggata: la pagina
porta fwuid, l'id 'loaded' e - offuscato in una chiave JSON letta al
contrario ("eikoocnekot" = "tokencookie") - il NOME di un cookie che
contiene il vero aura.token da mandare in ogni chiamata successiva
(api.py). Tutti e quattro (fwuid/loaded/token/i cookie di sessione) vanno
riletti ad ogni login: non sono hardcodabili (fwuid cambia ad ogni
release Salesforce, il nome del cookie-token non è garantito restare
'__Host-ERIC_PROD-<numero>').

Non essendoci un refresh_token (a differenza di E-Distribuzione), il
coordinator rifà login da zero ad ogni ciclo (una volta al giorno):
evita di dover gestire la scadenza della sessione a metà catena, al
prezzo di qualche richiesta in più che a questa cadenza è irrilevante.
"""
from __future__ import annotations

import logging
import re
import ssl
from dataclasses import dataclass
from urllib.parse import quote

import aiohttp

from .const import BASE_URL, HOME_URL, LOGIN_URL

_LOGGER = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# Pagina di atterraggio dopo il login: qualunque pagina valida del
# portale va bene (verificato con questa), non deve necessariamente
# essere la home.
_REF_URL = f"{BASE_URL}/portaleareti/CommunitiesLanding"

# Il server di Areti manda in handshake TLS SOLO il proprio certificato,
# senza l'intermedio che completa la catena fino alla radice attendibile
# (verificato con 'openssl s_client -showcerts' il 04/09/2026, su tutti e
# 3 gli IP edge dietro cui gira il sito - sistematico, non un caso
# isolato). Un client "nudo" (verificato: un Python appena installato da
# python.org su macOS) fallisce con "unable to get local issuer
# certificate" nonostante il certificato del sito sia valido - non è un
# problema del client né un certificato non valido, è il server che non
# completa la catena. Stesso rischio in un ambiente Home Assistant
# minimale (es. container senza cache di intermedi). Fix: aggiungere
# esplicitamente l'intermedio mancante (pubblico, scaricato una tantum
# dall'URL "CA Issuers" del certificato stesso, cacerts.digicert.com) al
# contesto SSL della sessione dedicata Areti. Dettagli completi in
# documentation/areti-protocol.md, "Gotcha TLS".
_DIGICERT_G2_TLS_RSA_SHA256_2020_CA1 = """-----BEGIN CERTIFICATE-----
MIIEyDCCA7CgAwIBAgIQDPW9BitWAvR6uFAsI8zwZjANBgkqhkiG9w0BAQsFADBh
MQswCQYDVQQGEwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3
d3cuZGlnaWNlcnQuY29tMSAwHgYDVQQDExdEaWdpQ2VydCBHbG9iYWwgUm9vdCBH
MjAeFw0yMTAzMzAwMDAwMDBaFw0zMTAzMjkyMzU5NTlaMFkxCzAJBgNVBAYTAlVT
MRUwEwYDVQQKEwxEaWdpQ2VydCBJbmMxMzAxBgNVBAMTKkRpZ2lDZXJ0IEdsb2Jh
bCBHMiBUTFMgUlNBIFNIQTI1NiAyMDIwIENBMTCCASIwDQYJKoZIhvcNAQEBBQAD
ggEPADCCAQoCggEBAMz3EGJPprtjb+2QUlbFbSd7ehJWivH0+dbn4Y+9lavyYEEV
cNsSAPonCrVXOFt9slGTcZUOakGUWzUb+nv6u8W+JDD+Vu/E832X4xT1FE3LpxDy
FuqrIvAxIhFhaZAmunjZlx/jfWardUSVc8is/+9dCopZQ+GssjoP80j812s3wWPc
3kbW20X+fSP9kOhRBx5Ro1/tSUZUfyyIxfQTnJcVPAPooTncaQwywa8WV0yUR0J8
osicfebUTVSvQpmowQTCd5zWSOTOEeAqgJnwQ3DPP3Zr0UxJqyRewg2C/Uaoq2yT
zGJSQnWS+Jr6Xl6ysGHlHx+5fwmY6D36g39HaaECAwEAAaOCAYIwggF+MBIGA1Ud
EwEB/wQIMAYBAf8CAQAwHQYDVR0OBBYEFHSFgMBmx9833s+9KTeqAx2+7c0XMB8G
A1UdIwQYMBaAFE4iVCAYlebjbuYP+vq5Eu0GF485MA4GA1UdDwEB/wQEAwIBhjAd
BgNVHSUEFjAUBggrBgEFBQcDAQYIKwYBBQUHAwIwdgYIKwYBBQUHAQEEajBoMCQG
CCsGAQUFBzABhhhodHRwOi8vb2NzcC5kaWdpY2VydC5jb20wQAYIKwYBBQUHMAKG
NGh0dHA6Ly9jYWNlcnRzLmRpZ2ljZXJ0LmNvbS9EaWdpQ2VydEdsb2JhbFJvb3RH
Mi5jcnQwQgYDVR0fBDswOTA3oDWgM4YxaHR0cDovL2NybDMuZGlnaWNlcnQuY29t
L0RpZ2lDZXJ0R2xvYmFsUm9vdEcyLmNybDA9BgNVHSAENjA0MAsGCWCGSAGG/WwC
ATAHBgVngQwBATAIBgZngQwBAgEwCAYGZ4EMAQICMAgGBmeBDAECAzANBgkqhkiG
9w0BAQsFAAOCAQEAkPFwyyiXaZd8dP3A+iZ7U6utzWX9upwGnIrXWkOH7U1MVl+t
wcW1BSAuWdH/SvWgKtiwla3JLko716f2b4gp/DA/JIS7w7d7kwcsr4drdjPtAFVS
slme5LnQ89/nD/7d+MS5EHKBCQRfz5eeLjJ1js+aWNJXMX43AYGyZm0pGrFmCW3R
bpD0ufovARTFXFZkAdl9h6g4U5+LXUZtXMYnhIHUfoyMo5tS58aI7Dd8KvvwVVo4
chDYABPPTHPbqjc1qCmBaZx2vN4Ye5DUys/vZwP9BFohFrH/6j/f3IL16/RZkiMN
JCqVJUzKoZHm1Lesh3Sz8W2jmdv51b2EQJ8HmA==
-----END CERTIFICATE-----
"""


def build_ssl_context() -> ssl.SSLContext:
    """Contesto SSL con l'intermedio DigiCert mancante aggiunto a quelli
    di sistema (load_verify_locations AGGIUNGE, non sostituisce quanto
    già caricato da create_default_context) - vedi il commento sopra."""
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(cadata=_DIGICERT_G2_TLS_RSA_SHA256_2020_CA1)
    return ctx


class AretiAuthError(Exception):
    """Errore generico di autenticazione."""


class AretiInvalidCredentials(AretiAuthError):
    """Email o password rifiutate.

    Euristica, non confermata su un caso reale (non abbiamo mai catturato
    un login fallito): trattiamo come credenziali non valide qualunque
    risposta del login POST priva dell'header 'Location' atteso. Se
    emerge un caso reale diverso (es. corpo con un messaggio d'errore
    esplicito, o un passaggio OTP mai visto prima), va distinto qui.
    """


class AretiParsingError(AretiAuthError):
    """La pagina scaricata non conteneva quello che ci aspettavamo.

    Causa più probabile: Areti ha cambiato qualcosa nel markup del
    portale (pagina di login Visualforce, o la home Lightning). Vedi
    documentation/areti-protocol.md per una cattura HAR di riferimento
    con cui confrontare una nuova.
    """


@dataclass
class AretiAuraContext:
    """Tutto il necessario per chiamare l'endpoint Aura (api.py) dopo un
    login riuscito."""

    fwuid: str
    loaded_app_id: str
    token: str


def _estrai_campo_hidden(html: str, nome_campo: str) -> str:
    """Estrae il 'value' di un <input type="hidden" name="..."> dalla
    pagina di login Visualforce/JSF. Non assume un ordine fisso degli
    attributi, solo che 'value' segua 'name' nello stesso tag."""
    match = re.search(rf'name="{re.escape(nome_campo)}"[^>]*\bvalue="([^"]*)"', html)
    if not match:
        raise AretiParsingError(
            f"Campo '{nome_campo}' non trovato nella pagina di login - la "
            "struttura è probabilmente cambiata rispetto a quanto "
            "documentato in documentation/areti-protocol.md."
        )
    return match.group(1)


class AretiAuthClient:
    """Esegue login + costruisce il contesto Aura su una sessione data.

    La sessione (con la sua jar di cookie) è responsabilità del
    chiamante: va creata con build_ssl_context() (vedi sopra) e riusata
    per tutte le chiamate Aura successive (api.py), perché i cookie di
    sessione (sid, sid_Client, il cookie-token) vivono lì, non in questo
    client.
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def async_login(self, email: str, password: str) -> AretiAuraContext:
        """Esegue l'intera catena di login e ritorna il contesto Aura."""
        headers = {"User-Agent": _USER_AGENT}

        async with self._session.get(LOGIN_URL, headers=headers) as resp:
            resp.raise_for_status()
            login_page_html = await resp.text()

        view_state = _estrai_campo_hidden(login_page_html, "com.salesforce.visualforce.ViewState")
        view_state_version = _estrai_campo_hidden(
            login_page_html, "com.salesforce.visualforce.ViewStateVersion"
        )
        view_state_mac = _estrai_campo_hidden(login_page_html, "com.salesforce.visualforce.ViewStateMAC")

        dati = {
            "AJAXREQUEST": "_viewRoot",
            "loginPage:loginForm": "loginPage:loginForm",
            "loginPage:loginForm:login-email": email,
            "loginPage:loginForm:login-password": password,
            "com.salesforce.visualforce.ViewState": view_state,
            "com.salesforce.visualforce.ViewStateVersion": view_state_version,
            "com.salesforce.visualforce.ViewStateMAC": view_state_mac,
            "loginPage:loginForm:j_id3": "loginPage:loginForm:j_id3",
        }
        login_headers = {
            **headers,
            "Origin": BASE_URL,
            "Referer": LOGIN_URL,
        }
        async with self._session.post(
            f"{LOGIN_URL}?refURL={quote(_REF_URL, safe='')}",
            data=dati,
            headers=login_headers,
            allow_redirects=False,
        ) as resp:
            location = resp.headers.get("Location")

        if not location:
            raise AretiInvalidCredentials(
                "Login rifiutato: nessun header 'Location' nella risposta "
                "(vedi AretiInvalidCredentials per i limiti di questa euristica)."
            )

        async with self._session.get(
            location, headers={**headers, "Referer": LOGIN_URL}
        ) as resp:
            resp.raise_for_status()

        if not self._session.cookie_jar.filter_cookies(BASE_URL).get("sid"):
            raise AretiAuthError(
                "Il ticket-exchange (frontdoor.jsp) non ha impostato il cookie "
                "'sid': il login potrebbe non essere davvero riuscito nonostante "
                "l'header Location fosse presente."
            )

        return await self._async_carica_contesto_aura()

    async def _async_carica_contesto_aura(self) -> AretiAuraContext:
        """Rilegge fwuid + il nome (offuscato) del cookie-token dalla home
        loggata. Vedi il docstring del modulo per il trucco
        'eikoocnekot' = 'tokencookie' al contrario."""
        headers = {"User-Agent": _USER_AGENT, "Referer": LOGIN_URL}
        async with self._session.get(HOME_URL, headers=headers) as resp:
            resp.raise_for_status()
            html = await resp.text()

        fwuid = _estrai_fwuid(html)
        loaded_app_id = _estrai_loaded_app_id(html)
        nome_cookie_token = _estrai_nome_cookie_token(html)

        cookie_token = self._session.cookie_jar.filter_cookies(BASE_URL).get(nome_cookie_token)
        if cookie_token is None:
            raise AretiAuthError(
                f"Il cookie '{nome_cookie_token}' (che dovrebbe portare aura.token) "
                "non è presente nella sessione dopo il caricamento della home."
            )

        return AretiAuraContext(fwuid=fwuid, loaded_app_id=loaded_app_id, token=cookie_token.value)


def _estrai_fwuid(html: str) -> str:
    """Id di build del framework Aura, cambia a ogni release Salesforce -
    va riletto ad ogni sessione, mai hardcodato."""
    match = re.search(r'"fwuid":"([^"]+)"', html)
    if not match:
        raise AretiParsingError("fwuid non trovato nella home (/s/) - pagina cambiata?")
    return match.group(1)


def _estrai_loaded_app_id(html: str) -> str:
    match = re.search(r'"APPLICATION@markup://siteforce:communityApp":"([^"]+)"', html)
    if not match:
        raise AretiParsingError("Id applicazione ('loaded') non trovato nella home.")
    return match.group(1)


def _estrai_nome_cookie_token(html: str) -> str:
    """Il nome del campo JSON è offuscato ('eikoocnekot' = 'tokencookie'
    letto al contrario, trucco noto di Aura): il suo VALORE è il nome del
    cookie che porta il vero aura.token, non un id fisso."""
    match = re.search(r'"eikoocnekot":"([^"]+)"', html)
    if not match:
        raise AretiParsingError("Nome del cookie-token non trovato nella home.")
    return match.group(1)
