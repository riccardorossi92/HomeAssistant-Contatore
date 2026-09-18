"""Raccolta dati per il supporto Ireti (smartpod.ireti.it) in contatore_letture.

Serve a chi HA una fornitura Ireti attiva e vuole aiutare a implementare
il supporto: lo script si collega, scopre quali API espone il portale e
salva un report ANONIMIZZATO da allegare a una issue su GitHub.

COSA FA
  1. Login (password grant Keycloak - verificato funzionante il 31/08/2026).
  2. Legge la tua anagrafica e l'elenco dei tuoi POD.
  3. Scarica il bundle JavaScript dell'app Angular (pubblico, lo scarica
     anche il tuo browser ogni volta che apri il sito) ed estrae l'elenco
     degli endpoint che l'app sa chiamare: e' cosi' che scopriamo gli
     endpoint dei consumi senza doverli indovinare.
  4. Prova con POST i quattro endpoint di misura confermati
     (exabeat/history, exabeat/registry, readings/exabeat/measures,
     readings/exabeat/measures-loadprofiles) con il body corretto.
  5. Prova anche gli altri path del bundle che sembrano di misura, come
     esplorazione, e registra la STRUTTURA delle risposte.

COSA NON FA
  Non invia niente a nessuno. Scrive solo un file locale che decidi tu se
  e a chi mandare.

PRIVACY
  Il report e' anonimizzato automaticamente: nome, cognome, codice
  fiscale, email, telefono, indirizzi, token e identificativi personali
  vengono sostituiti da segnaposto; il codice POD e' mascherato
  mantenendo solo il formato. Dei dati di consumo vengono tenuti pochi
  campioni, perche' serve capire la struttura (nomi dei campi, unita' di
  misura), non i tuoi consumi reali. Prima di allegare il file, aprilo e
  controlla: se trovi qualcosa che non vuoi condividere, cancellalo -
  serve la forma delle risposte, non il loro contenuto.

USO
    pip install requests
    python3 raccogli_dati_ireti.py

Poi allega 'ireti_report.json' alla issue di supporto Ireti su
https://github.com/riccardorossi92/HomeAssistant-Contatore/issues
"""
from __future__ import annotations

import getpass
import json
import re
import sys
from datetime import date, datetime, timedelta
from typing import Any

try:
    import requests
except ImportError:
    sys.exit("Manca 'requests': installalo con  pip install requests")

BASE = "https://smartpod.ireti.it"
REALM = "IRENSmartPOD"
CLIENT_ID = "SmartPOD-Angular"
TOKEN_URL = f"{BASE}/auth/realms/{REALM}/protocol/openid-connect/token"

# Senza header da browser il backend /users/* non risponde affatto (va in
# timeout, non 403: il WAF scarta silenziosamente) - verificato il 31/08/2026.
HEADERS_BROWSER = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "it-IT,it;q=0.9",
    "Referer": f"{BASE}/prelievi",
    "Origin": BASE,
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}

# Chiavi il cui VALORE non deve mai finire nel report.
CHIAVI_DA_OSCURARE = {
    "name", "surname", "nome", "cognome", "ragsoc", "ragionesociale",
    "codfiscale", "codicefiscale", "cf", "piva", "partitaiva", "vatnumber",
    "customertaxcodevat",
    "email", "mail", "telnumber", "cellnumber", "telefono", "cellulare",
    "indirizzo", "address", "via", "civico", "cap", "citta", "comune",
    "access_token", "refresh_token", "id_token", "token", "password",
    "keycloakuserid", "keycloakuser", "username", "idconsumer", "sessionstate",
    "serialnumber",
}


def _oscura_valore(chiave: str, valore: Any) -> Any:
    if valore is None or isinstance(valore, bool):
        return valore
    testo = str(valore)
    if not testo:
        return valore
    return f"<{chiave}: {len(testo)} caratteri>"


def _maschera_pod(pod: str) -> str:
    """'IT001E1234567' -> 'IT###E#######': tiene il formato, non il codice."""
    return re.sub(r"\d", "#", pod)


def anonimizza(dato: Any, campioni_lista: int = 3) -> Any:
    """Copia ricorsiva con i valori sensibili sostituiti.

    Delle liste lunghe tiene solo i primi 'campioni_lista' elementi: serve
    la struttura, non il volume (una curva di consumo puo' avere migliaia
    di punti, e sono comunque dati personali).
    """
    if isinstance(dato, dict):
        pulito = {}
        for chiave, valore in dato.items():
            if chiave.lower().replace("_", "") in CHIAVI_DA_OSCURARE:
                pulito[chiave] = _oscura_valore(chiave, valore)
            elif "pod" in chiave.lower() and isinstance(valore, str) and valore:
                pulito[chiave] = _maschera_pod(valore)
            else:
                pulito[chiave] = anonimizza(valore, campioni_lista)
        return pulito
    if isinstance(dato, list):
        troncata = [anonimizza(v, campioni_lista) for v in dato[:campioni_lista]]
        if len(dato) > campioni_lista:
            troncata.append(f"<...altri {len(dato) - campioni_lista} elementi omessi...>")
        return troncata
    return dato


def login(username: str, password: str) -> str:
    print("\n[1/5] Login...")
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "password",
            "client_id": CLIENT_ID,
            "username": username,
            "password": password,
            "scope": "openid profile email",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        try:
            err = resp.json()
            sys.exit(f"  Login fallito: {err.get('error')} - {err.get('error_description')}")
        except ValueError:
            sys.exit(f"  Login fallito (HTTP {resp.status_code}): {resp.text[:200]}")
    dati = resp.json()
    print(f"  OK (token valido {dati.get('expires_in')}s, "
          f"refresh {dati.get('refresh_expires_in')}s)")
    return dati["access_token"], dati


def chiama(sess: requests.Session, nome: str, url: str, metodo: str = "GET",
           **kwargs) -> tuple[int | None, Any]:
    """Chiamata protetta: un endpoint che fallisce non interrompe il resto."""
    try:
        r = sess.request(metodo, url, timeout=25, **kwargs)
    except requests.exceptions.RequestException as exc:
        print(f"  {nome}: FALLITA ({type(exc).__name__})")
        return None, {"errore": type(exc).__name__}
    esito = "OK" if r.ok else "errore"
    print(f"  {nome}: HTTP {r.status_code} ({esito})")
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"_corpo_non_json": r.text[:300]}


def estrai_endpoint_dal_bundle(sess: requests.Session) -> list[str]:
    """Scarica il bundle Angular dell'app ed estrae i path di API.

    Il bundle e' un file pubblico servito dal sito (lo scarica anche il
    browser): contiene in chiaro le stringhe degli endpoint che l'app sa
    chiamare, quindi ci dice quali API esistono senza doverle indovinare.
    """
    print("\n[3/5] Estrazione endpoint dal bundle JavaScript dell'app...")
    r = sess.get(f"{BASE}/prelievi", timeout=25)
    bundle = re.findall(r'src="(/?main-es\d+\.[a-f0-9]+\.js)"', r.text)
    if not bundle:
        bundle = re.findall(r'"(/?main[^"]*\.js)"', r.text)
    if not bundle:
        print("  Bundle non trovato nella pagina (struttura del sito cambiata?)")
        return []

    url_bundle = BASE + ("" if bundle[0].startswith("/") else "/") + bundle[0]
    print(f"  Bundle: {bundle[0]}")
    rb = sess.get(url_bundle, timeout=60)
    if not rb.ok:
        print(f"  Download fallito: HTTP {rb.status_code}")
        return []
    print(f"  Scaricato ({len(rb.text)} caratteri), cerco i path delle API...")

    # Path che iniziano per / e somigliano a endpoint REST, esclusi asset.
    # "readings" e' incluso apposta: e' li' che vivono gli endpoint di
    # misura (/readings/exabeat/measures-loadprofiles, confermato il
    # 17/09/2026 - prima di aggiungere "readings" qui il path veniva
    # scartato e lo script non lo trovava mai da solo.
    grezzi = set(re.findall(
        r'"(/(?:users|readings|misure|pod|prelievi|consumi|api)[a-zA-Z0-9/_\-{}.]*)"',
        rb.text,
    ))
    esclusi = (".js", ".css", ".png", ".svg", ".woff", ".ico", ".html")
    trovati = sorted(p for p in grezzi if not p.endswith(esclusi))
    print(f"  Trovati {len(trovati)} path candidati")
    return trovati


def _finestra_ultimo_mese_completo() -> tuple[str, str]:
    """Primo e ultimo istante dell'ultimo mese completo, in UTC ISO 8601.

    Stesso formato usato dal frontend (visto in issue #6): startDate a
    mezzanotte del primo giorno del mese, endDate a fine dell'ultimo -
    entrambi come istanti UTC.
    """
    oggi = date.today()
    fine_mese_scorso = oggi.replace(day=1) - timedelta(days=1)
    inizio_mese_scorso = fine_mese_scorso.replace(day=1)
    inizio = datetime(
        inizio_mese_scorso.year, inizio_mese_scorso.month, inizio_mese_scorso.day,
        tzinfo=datetime.UTC,
    )
    fine = datetime(
        fine_mese_scorso.year, fine_mese_scorso.month, fine_mese_scorso.day,
        23, 59, 59, tzinfo=datetime.UTC,
    )

    def iso(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    return iso(inizio), iso(fine)


# Endpoint di misura noti dal bundle, provati esplicitamente con POST e il
# body corretto (non con la scansione generica GET+query, sbagliata per
# questi: e' quello che ha impedito allo script di trovarli da solo finora).
ENDPOINT_MISURA = [
    "/users/exabeat/history",
    "/users/exabeat/registry",
    "/readings/exabeat/measures",
    "/readings/exabeat/measures-loadprofiles",
]


def main() -> None:
    print(__doc__.split("USO")[0].strip()[:200] + "...\n")
    username = input("Username Ireti: ").strip()
    password = getpass.getpass("Password (non visibile): ")

    token, token_info = login(username, password)

    sess = requests.Session()
    sess.headers.update({**HEADERS_BROWSER, "Authorization": f"Bearer {token}"})

    report: dict[str, Any] = {
        "generato_da": "raccogli_dati_ireti.py",
        "note": "Report anonimizzato per l'implementazione del supporto Ireti.",
        "token_info": {
            "expires_in": token_info.get("expires_in"),
            "refresh_expires_in": token_info.get("refresh_expires_in"),
            "scope": token_info.get("scope"),
        },
        "chiamate": {},
        "endpoint_dal_bundle": [],
    }

    print("\n[2/5] Anagrafica e POD...")
    st, company = chiama(sess, "company-by-host", f"{BASE}/users/public/company-by-host")
    report["chiamate"]["company-by-host"] = {"status": st, "risposta": anonimizza(company)}
    id_company = company.get("idCompany") if isinstance(company, dict) else None

    st, consumer_raw = chiama(
        sess, "consumer", f"{BASE}/users/consumer/getbykeycloakusername/{username}"
    )
    report["chiamate"]["consumer"] = {"status": st, "risposta": anonimizza(consumer_raw)}
    consumer = {}
    if isinstance(consumer_raw, dict):
        modelli = consumer_raw.get("entityModel") or []
        consumer = modelli[0] if modelli else {}
    id_consumer = consumer.get("idConsumer")

    pod_utente: list[str] = []
    if id_consumer and id_company:
        st, pods = chiama(
            sess, "elenco POD",
            f"{BASE}/users/pods/getallbyconsumerandcompany/{id_consumer}/{id_company}",
        )
        report["chiamate"]["elenco-pod"] = {"status": st, "risposta": anonimizza(pods)}
        if isinstance(pods, dict):
            for voce in (pods.get("entityModel") or []):
                if isinstance(voce, dict):
                    for chiave in ("pod", "podCode", "codicePod", "idPod", "name"):
                        if voce.get(chiave):
                            pod_utente.append(str(voce[chiave]))
                            break

    if pod_utente:
        print(f"  POD trovati: {len(pod_utente)}")
    else:
        print("  ATTENZIONE: nessun POD trovato sull'account.")
        print("  Il report sara' comunque utile (endpoint disponibili), ma senza")
        print("  le risposte dei consumi, che sono la parte piu' importante.")

    report["endpoint_dal_bundle"] = estrai_endpoint_dal_bundle(sess)

    print("\n[4/5] Prova degli endpoint di misura noti (exabeat/readings)...")
    customer_tax_code_vat = consumer.get("pIVA") or consumer.get("codFiscale")
    pod_type = None
    start_iso, end_iso = _finestra_ultimo_mese_completo()
    if pod_utente and customer_tax_code_vat:
        pod_code = pod_utente[0]

        st, history = chiama(
            sess, "/users/exabeat/history", f"{BASE}/users/exabeat/history",
            metodo="POST",
            json={
                "customerTaxCodeVat": customer_tax_code_vat,
                "pod": pod_code,
                "startDate": start_iso,
                "endDate": end_iso,
            },
        )
        report["chiamate"]["/users/exabeat/history"] = {"status": st, "risposta": anonimizza(history)}
        if isinstance(history, dict):
            pod_type = history.get("podType")

        for path in ("/users/exabeat/registry", "/readings/exabeat/measures",
                      "/readings/exabeat/measures-loadprofiles"):
            body: dict[str, Any] = {
                "customerTaxCodeVat": customer_tax_code_vat,
                "pod": pod_code,
                "startDate": start_iso,
                "endDate": end_iso,
            }
            if path.startswith("/readings/"):
                body["operation"] = "PRELIEVO"
                if pod_type:
                    body["podType"] = pod_type
            st, risp = chiama(sess, path, f"{BASE}{path}", metodo="POST", json=body)
            report["chiamate"][path] = {"status": st, "risposta": anonimizza(risp)}
    elif not pod_utente:
        for path in ENDPOINT_MISURA:
            report["chiamate"][path] = {"nota": "saltato: nessun POD sull'account"}
    else:
        for path in ENDPOINT_MISURA:
            report["chiamate"][path] = {"nota": "saltato: codice fiscale/P.IVA non trovato in anagrafica"}

    print("\n[5/5] Altri path candidati dal bundle (esplorativo, POST con body vuoto)...")
    candidati = [
        p for p in report["endpoint_dal_bundle"]
        if p not in ENDPOINT_MISURA
        and any(k in p.lower() for k in
                ("misur", "consum", "prelie", "curva", "load", "letture", "energ", "chart"))
    ]
    if candidati:
        print(f"  {len(candidati)} candidati:")
        for p in candidati:
            print(f"    {p}")
    else:
        print("  Nessun candidato ulteriore tra i path estratti.")

    if pod_utente and candidati:
        for path in candidati[:8]:
            if "{" in path:  # path con segnaposto: non sappiamo cosa metterci
                report["chiamate"][path] = {"nota": "path parametrico, non provato"}
                continue
            url = f"{BASE}{path}"
            # Non sappiamo il payload atteso: proviamo POST con un body
            # generico simile a quello confermato per gli endpoint exabeat.
            # Un errore qui (400/422) finisce comunque nel report ed e' gia'
            # un'informazione utile (conferma che l'endpoint esiste).
            st, risp = chiama(
                sess, path, url, metodo="POST",
                json={
                    "customerTaxCodeVat": customer_tax_code_vat,
                    "pod": pod_utente[0],
                    "startDate": start_iso,
                    "endDate": end_iso,
                },
            )
            report["chiamate"][path] = {"status": st, "risposta": anonimizza(risp)}

    percorso = "ireti_report.json"
    with open(percorso, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n=== Report salvato in {percorso} ===")
    print("PRIMA DI INVIARLO: aprilo e dai un'occhiata. E' anonimizzato in")
    print("automatico, ma il controllo finale spetta a te - se vedi qualcosa")
    print("che non vuoi condividere, cancellalo pure: serve la struttura")
    print("delle risposte, non il loro contenuto.")


if __name__ == "__main__":
    main()
