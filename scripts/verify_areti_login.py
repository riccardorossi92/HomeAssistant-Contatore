"""Testa da terminale il login Areti e il recupero della curva di carico,
senza Home Assistant nel mezzo - implementa la catena documentata in
documentation/areti-protocol.md, per verificarla contro il portale reale
prima (e mentre) si scrive il modulo
custom_components/contatore_letture/distributors/areti/.

Copre l'intera catena osservata su cattura reale (04/09/2026):
  login Visualforce/JSF (AretiLoginURL) -> ticket-exchange (frontdoor.jsp)
  -> lettura di fwuid/aura.token dalla home Lightning -> chiamate Aura
  ARIA_DatiDiMisuraController/ARIA_DatiMisuraGetMisurazioni_WS -> curva
  di carico a 15 minuti per un mese scelto.

Controlla anche, sempre, la disponibilita' del MESE IN CORSO (non solo
quello scelto): serve a capire il ritardo reale dei dati Areti (quanti
giorni fa e' l'ultimo giorno disponibile), utile per decidere se il
coordinator potra' importare a giorni (come fa gia' async_valida_pod sui
PCF, RITARDO_DATI_GIORNI=1) o se conviene aspettare il mese chiuso (come
il coordinator PCF fa per scelta di design, non per limite dell'API -
vedi pcf_common/coordinator.py:_mese_precedente_completo). Riporta
l'ultimo giorno con dati e il ritardo in giorni rispetto a oggi.

Non importa nulla da custom_components/ (che dipende da Home Assistant):
implementa il protocollo da zero con 'requests', sullo stesso principio
di scripts/raccogli_dati_ireti.py - una volta che il modulo vero esiste,
lo script giusto per iterare velocemente e' verify_edistribuzione_login.py
(che invece IMPORTA auth.py/api.py reali).

Uso:
    pip install requests
    python3 verify_areti_login.py

La password viene letta con getpass (non appare a schermo, non resta
nella history del terminale).
"""
from __future__ import annotations

import getpass
import json
import re
import sys
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import quote

try:
    import requests
except ImportError:
    sys.exit("Manca 'requests': installalo con  pip install requests")

# Il server di Areti manda in handshake TLS SOLO il proprio certificato,
# senza il certificato intermedio della catena (verificato con
# `openssl s_client -showcerts`: la catena servita e' lunga 1). Un
# browser lo completa da solo (Authority Information Access + intermedi
# gia' in cache da altri siti DigiCert), un client non-browser come
# 'requests' no: fallisce con "unable to get local issuer certificate"
# anche con un certificate store perfettamente aggiornato - non e' un
# problema del Python locale ne' un certificato del sito non valido, e'
# il server che non manda la catena completa. Fix: aggiungere
# esplicitamente l'intermedio mancante (scaricato una volta dall'URL
# "CA Issuers" del certificato del sito, cacerts.digicert.com - e'
# pubblico, non e' un segreto) al bundle di root CA di certifi.
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


def _prepara_ca_bundle() -> str | bool:
    """Bundle di root CA di certifi + l'intermedio DigiCert mancante,
    scritto in un file temporaneo (fuori dal repository) e riusato tra
    un lancio e l'altro. Se certifi non e' disponibile, torna True
    (verifica col certificate store di sistema - meglio di niente)."""
    try:
        import certifi
    except ImportError:
        return True
    combinato = Path(tempfile.gettempdir()) / "areti_ca_bundle.pem"
    testo = Path(certifi.where()).read_text(encoding="utf-8")
    testo += "\n" + _DIGICERT_G2_TLS_RSA_SHA256_2020_CA1
    combinato.write_text(testo, encoding="utf-8")
    return str(combinato)


CA_BUNDLE = _prepara_ca_bundle()

BASE = "https://areariservataclienti.areti.it"
LOGIN_URL = f"{BASE}/portaleareti/AretiLoginURL"
AURA_URL = f"{BASE}/portaleareti/s/sfsites/aura"
HOME_URL = f"{BASE}/portaleareti/s/"
# Pagina dopo il login: qualunque pagina valida del sito va bene, non deve
# essere per forza questa - verificato che funziona su cattura reale.
REF_URL = f"{BASE}/portaleareti/CommunitiesLanding"

HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "it-IT,it;q=0.9",
    "Origin": BASE,
}


class AretiAuthError(RuntimeError):
    """Login fallito, o pagina cambiata rispetto a quanto documentato in
    documentation/areti-protocol.md."""


def _estrai_campo_hidden(html: str, nome_campo: str) -> str:
    """Estrae il 'value' di un <input type="hidden" name="..."> dalla
    pagina di login Visualforce/JSF. Il regex non assume un ordine fisso
    degli attributi dell'input, solo che 'value' segua 'name' nello
    stesso tag."""
    m = re.search(rf'name="{re.escape(nome_campo)}"[^>]*\bvalue="([^"]*)"', html)
    if not m:
        raise AretiAuthError(
            f"Campo '{nome_campo}' non trovato nella pagina di login - la "
            "struttura della pagina e' probabilmente cambiata rispetto a "
            "quanto documentato in documentation/areti-protocol.md."
        )
    return m.group(1)


def login(sess: requests.Session, email: str, password: str) -> None:
    print("\n[1/3] Carico la pagina di login (AretiLoginURL)...")
    try:
        r = sess.get(LOGIN_URL, headers=HEADERS_BASE, timeout=30)
    except requests.exceptions.SSLError as exc:
        raise AretiAuthError(
            "Verifica del certificato HTTPS fallita nonostante il bundle CA "
            "con l'intermedio DigiCert incluso in questo script - se vedi "
            "questo errore, o e' cambiato il certificato del sito (nuova CA "
            "emittente) o 'certifi' non e' installato. Prova:\n"
            "  1. pip3 install --upgrade certifi\n"
            "  2. su macOS, come ultima risorsa: Applicazioni -> cartella "
            "'Python 3.x' -> 'Install Certificates.command'\n"
            f"Dettaglio originale: {exc}"
        ) from exc
    r.raise_for_status()
    html = r.text

    view_state = _estrai_campo_hidden(html, "com.salesforce.visualforce.ViewState")
    view_state_version = _estrai_campo_hidden(
        html, "com.salesforce.visualforce.ViewStateVersion"
    )
    view_state_mac = _estrai_campo_hidden(html, "com.salesforce.visualforce.ViewStateMAC")

    print("[2/3] Invio email/password...")
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
    r = sess.post(
        f"{LOGIN_URL}?refURL={quote(REF_URL, safe='')}",
        data=dati,
        headers={**HEADERS_BASE, "Referer": LOGIN_URL},
        timeout=30,
        allow_redirects=False,
    )
    location = r.headers.get("Location")
    if not location:
        raise AretiAuthError(
            "Login rifiutato: nessun header 'Location' nella risposta (status "
            f"{r.status_code}). Probabile causa: credenziali errate, oppure e' "
            "comparso un passaggio OTP/MFA mai osservato nella cattura di "
            "riferimento (non gestito da questo script - se succede, serve "
            "una nuova cattura HAR di quel passaggio)."
        )
    print("  OK: email/password accettate (nessun OTP nel flusso osservato).")

    print("[3/3] Scambio del ticket di sessione (frontdoor.jsp)...")
    r = sess.get(location, headers={**HEADERS_BASE, "Referer": LOGIN_URL}, timeout=30)
    r.raise_for_status()
    if "sid" not in sess.cookies.get_dict():
        raise AretiAuthError(
            "Il ticket-exchange non ha impostato il cookie 'sid': il login "
            "potrebbe non essere davvero riuscito nonostante l'header "
            "Location fosse presente."
        )
    print("  OK: sessione stabilita (cookie 'sid' presente).")


def carica_contesto_aura(sess: requests.Session) -> dict:
    """Rilegge fwuid + il nome (offuscato) del cookie che porta il vero
    aura.token dalla home loggata. Vanno riletti ad ogni sessione: fwuid
    cambia ad ogni release Salesforce (qualche volta l'anno), e il nome
    del cookie-token non e' fisso (osservato '__Host-ERIC_PROD-<numero>'
    nella cattura, ma non c'e' garanzia resti quel prefisso)."""
    print("\nLeggo fwuid e token CSRF dalla home (/s/)...")
    r = sess.get(HOME_URL, headers={**HEADERS_BASE, "Referer": LOGIN_URL}, timeout=30)
    r.raise_for_status()
    html = r.text

    m = re.search(r'"fwuid":"([^"]+)"', html)
    if not m:
        raise AretiAuthError("fwuid non trovato nella home - pagina cambiata?")
    fwuid = m.group(1)

    m = re.search(r'"APPLICATION@markup://siteforce:communityApp":"([^"]+)"', html)
    if not m:
        raise AretiAuthError("Id applicazione ('loaded') non trovato nella home.")
    loaded_app_id = m.group(1)

    # Il nome del campo JSON e' offuscato ("eikoocnekot" = "tokencookie"
    # letto al contrario, trucco noto di Aura): il suo VALORE e' il nome
    # del cookie che porta il vero aura.token, non un id fisso.
    m = re.search(r'"eikoocnekot":"([^"]+)"', html)
    if not m:
        raise AretiAuthError("Nome del cookie-token non trovato nella home.")
    nome_cookie_token = m.group(1)

    token = sess.cookies.get(nome_cookie_token)
    if not token:
        raise AretiAuthError(
            f"Il cookie '{nome_cookie_token}' (che dovrebbe portare aura.token) "
            "non e' presente nella sessione dopo il caricamento della home."
        )

    print(f"  OK: fwuid letto, aura.token dal cookie '{nome_cookie_token}'.")
    return {"fwuid": fwuid, "loaded_app_id": loaded_app_id, "token": token}


_contatore_r = 0


def chiama_apex(
    sess: requests.Session, contesto: dict, classname: str, method: str,
    params: dict | None = None,
) -> dict:
    """Una chiamata aura://ApexActionController/ACTION$execute (il
    descriptor generico usato da tutti gli endpoint ARIA_* documentati).
    Ritorna direttamente il valore di ritorno del metodo Apex (l'involucro
    '{"returnValue": ..., "cacheable": ...}' di Salesforce viene tolto
    qui) - solleva se lo stato non e' SUCCESS."""
    global _contatore_r
    _contatore_r += 1

    action_params = {
        "namespace": "",
        "classname": classname,
        "method": method,
        "cacheable": False,
        "isContinuation": False,
    }
    if params is not None:
        action_params["params"] = params

    message = json.dumps(
        {
            "actions": [
                {
                    "id": f"{_contatore_r};a",
                    "descriptor": "aura://ApexActionController/ACTION$execute",
                    "callingDescriptor": "UNKNOWN",
                    "params": action_params,
                }
            ]
        }
    )
    aura_context = json.dumps(
        {
            "mode": "PROD",
            "fwuid": contesto["fwuid"],
            "app": "siteforce:communityApp",
            "loaded": {
                "APPLICATION@markup://siteforce:communityApp": contesto["loaded_app_id"]
            },
            "dn": [],
            "globals": {},
            "uad": True,
        }
    )

    r = sess.post(
        AURA_URL,
        params={"r": _contatore_r, "aura.ApexAction.execute": 1},
        data={
            "message": message,
            "aura.context": aura_context,
            "aura.pageURI": "/portaleareti/s/",
            "aura.token": contesto["token"],
        },
        headers={**HEADERS_BASE, "Referer": HOME_URL},
        timeout=30,
    )
    r.raise_for_status()
    corpo = r.json()
    azione = corpo["actions"][0]
    if azione["state"] != "SUCCESS":
        raise RuntimeError(f"{classname}.{method} fallita: {azione.get('error')}")
    return azione["returnValue"]["returnValue"]


def recupera_mese(
    sess: requests.Session, contesto: dict, pod: str, codice_bp: str,
    codice_fiscale: str, componente: str, mese_anno: str,
) -> dict | None:
    """Chiama getMisurazioni per un meseAnno (MMYYYY) e ritorna il primo
    elemento di misureByBP già deserializzato, o None se il portale non
    ha nulla per quel mese (esito negativo o lista vuota - NON solleva in
    questo caso, e' un esito legittimo per un mese senza dati)."""
    input_params_json = json.dumps(
        {
            "useMock": False,
            "meseAnno": mese_anno,
            "codiceBP": codice_bp,
            "codiceFiscale": codice_fiscale,
            "pod": pod,
            "componenteEnergia": componente,
        }
    )
    misure_raw = chiama_apex(
        sess, contesto, "ARIA_DatiMisuraGetMisurazioni_WS", "getMisurazioni",
        {"inputParamsJson": input_params_json},
    )
    if not misure_raw.get("esito"):
        print(f"  Nessun dato per {mese_anno}: {misure_raw.get('errorMessage') or '(nessun messaggio nella risposta)'}")
        return None
    misure = json.loads(misure_raw["misureByBP"])
    if not misure:
        print(f"  Risposta OK ma lista vuota per {mese_anno}: nessun dato ancora disponibile.")
        return None
    return misure[0]


def giorni_con_dati(elementi: list[dict]) -> list[str]:
    """Giorni (YYYY-MM-DD, ordinati) per cui 'elementi' ha almeno un
    Value non nullo/vuoto.

    Euristica, non una garanzia: un consumo davvero a zero per tutti i
    quarti d'ora di un giorno è indistinguibile da un giorno il cui dato
    non è ancora arrivato (l'API non sembra marcarlo esplicitamente). Un
    giorno completamente ASSENTE dalla lista, o con OGNI valore nullo, è
    però un segnale forte di 'non ancora disponibile' - è quello che
    interessa per capire il ritardo dei dati."""
    per_giorno: dict[str, list] = {}
    for el in elementi:
        per_giorno.setdefault(el["Data"], []).append(el.get("Value"))
    return sorted(
        giorno for giorno, valori in per_giorno.items()
        if any(v not in (None, "") for v in valori)
    )


def main() -> None:
    email = input("Email Areti: ").strip()
    password = getpass.getpass("Password (non visibile mentre digiti): ")

    sess = requests.Session()
    sess.verify = CA_BUNDLE

    try:
        login(sess, email, password)
        contesto = carica_contesto_aura(sess)
    except AretiAuthError as exc:
        sys.exit(f"\nERRORE: {exc}")

    print("\n=== LOGIN COMPLETO RIUSCITO ===")

    pod = input("\nPOD da interrogare (invio per fermarmi qui): ").strip()
    if not pod:
        print("Nessun POD indicato - il login funziona, mi fermo qui.")
        return

    print(f"\n--- Configurazione misure per {pod} ---")
    try:
        config = chiama_apex(
            sess, contesto, "ARIA_DatiDiMisuraController", "getConfigurations",
            {"podName": pod},
        )
    except Exception:
        print("Errore IMPREVISTO in getConfigurations (traceback sotto):\n")
        raise
    codice_bp = config.get("codiceBP")
    codice_fiscale = config.get("codiceFiscale")
    if not codice_bp:
        sys.exit(
            f"\nNessun 'codiceBP' nella risposta per il POD {pod}: probabile "
            "che il POD non sia associato a questo account, o il formato non "
            "sia corretto."
        )
    print(f"  codiceBP: {codice_bp}")
    print(f"  is2G: {config.get('is2G')}")
    print(
        "  componenti energia disponibili: "
        f"{[o['value'] for o in config.get('energyOptions', [])]}"
    )

    oggi = date.today()
    mese_scorso_num = oggi.month - 1 or 12
    anno_mese_scorso = oggi.year if oggi.month > 1 else oggi.year - 1
    mese_scorso = f"{mese_scorso_num:02d}{anno_mese_scorso}"
    mese_corrente = f"{oggi.month:02d}{oggi.year}"

    mese_anno_input = input(
        f"\nMese da interrogare, formato MMYYYY [invio per il mese scorso, {mese_scorso}]: "
    ).strip()
    mese_anno = mese_anno_input or mese_scorso
    componente = input("Componente energia [invio per EA = prelievo]: ").strip() or "EA"

    print(f"\n--- Recupero misure {mese_anno} ({componente}) per {pod} ---")
    try:
        dettaglio = recupera_mese(
            sess, contesto, pod, codice_bp, codice_fiscale, componente, mese_anno
        )
    except Exception:
        print("Errore IMPREVISTO in getMisurazioni (traceback sotto):\n")
        raise
    if dettaglio is None:
        sys.exit(f"\nNessun dato disponibile per {mese_anno}, mi fermo qui.")

    curva = dettaglio.get("elementiCurve", [])
    aggregati = dettaglio.get("elementiAggregati", [])

    print("\n=== RISULTATO ===")
    print(f"elementiCurve (dettaglio a 15 min, atteso ~96/giorno): {len(curva)} punti")
    print(f"elementiAggregati (un totale per giorno): {len(aggregati)} punti")
    if curva:
        date_uniche = sorted({c["Data"] for c in curva})
        print(f"date coperte: {len(date_uniche)} (da {date_uniche[0]} a {date_uniche[-1]})")
        print("\nPrimi 4 punti della curva:")
        for c in curva[:4]:
            print(f"  {c['Data']} {c['Ora']}  {c['Value']}")
        somma_curva = sum(float(c["Value"]) for c in curva)
        print(f"\nConsumo del mese (somma di elementiCurve): {somma_curva:.3f} kWh")
    lettura_cumulativa = dettaglio.get("esitoPosizioneBP", {}).get("Tot_Active_EN")
    print(
        f"Lettura cumulativa del contatore a fine mese (Tot_Active_EN): "
        f"{lettura_cumulativa} - NON e' il consumo del mese, e' una lettura "
        "progressiva (verificato: puo' differire dalla somma della curva "
        "anche di un ordine di grandezza, vedi areti-protocol.md)"
    )

    debug_path = Path("areti_misure_debug.json")
    debug_path.write_text(
        json.dumps(dettaglio, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nRisposta completa salvata in {debug_path.resolve()}")
    print(
        "ATTENZIONE: questo file contiene dati REALI (POD, codice fiscale, "
        "consumi) - resta locale (gia' escluso da git), non condividerlo "
        "senza prima ripulirlo."
    )

    # --- Disponibilità del mese in corso: quanti giorni di ritardo? ---
    # Serve a capire se l'import automatico può coprire solo il mese
    # precedente completo o anche il mese in corso con 1+ giorni di
    # ritardo (come RITARDO_DATI_GIORNI in edistribuzione/const.py) -
    # controllato sempre, indipendentemente dal mese scelto sopra.
    print(f"\n--- Disponibilità del mese in corso ({mese_corrente}) ---")
    if mese_anno == mese_corrente:
        print("(è lo stesso mese appena interrogato sopra)")
        dettaglio_corrente = dettaglio
    else:
        try:
            dettaglio_corrente = recupera_mese(
                sess, contesto, pod, codice_bp, codice_fiscale, componente, mese_corrente
            )
        except Exception as exc:
            print(f"  Chiamata fallita per il mese in corso: {exc}")
            dettaglio_corrente = None

    if dettaglio_corrente is not None:
        giorni = giorni_con_dati(dettaglio_corrente.get("elementiCurve", []))
        print(f"  Giorni del mese in corso con almeno un dato: {len(giorni)}")
        if giorni:
            ultimo_giorno = date.fromisoformat(giorni[-1])
            ritardo = (oggi - ultimo_giorno).days
            print(
                f"  Ultimo giorno con dati: {giorni[-1]} "
                f"({ritardo} giorno/i di ritardo rispetto a oggi {oggi.isoformat()})"
            )
            print(
                "  => Ci sono dati per il mese in corso: Areti pubblica quindi "
                "qualcosa prima della chiusura del mese, non solo mesi chiusi."
            )
        else:
            print(
                "  Nessun giorno con dati ancora nel mese in corso (getMisurazioni "
                "OK ma vuota) - come osservato il 04/09/2026: Areti sembra "
                "pubblicare i dati per mese solare CHIUSO, non progressivamente "
                "durante il mese (diverso da RITARDO_DATI_GIORNI=1 dei PCF, dove "
                "e' il coordinator a scegliere comunque il mese completo per "
                "scelta di design, non per limite dell'API - vedi "
                "pcf_common/coordinator.py:_mese_precedente_completo)."
            )
    else:
        print(
            "  => Nessun dato per il mese in corso (dettaglio sopra): come "
            "osservato il 04/09/2026, Areti sembra pubblicare i dati per mese "
            "solare CHIUSO, non progressivamente durante il mese."
        )


if __name__ == "__main__":
    main()
