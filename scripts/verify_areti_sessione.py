"""Misura cosa risponde Areti quando la sessione non e' piu' valida.

Punto aperto di documentation/protocols/areti-protocol.md ("Comportamento a
sessione scaduta"): la forma esatta della risposta di /sfsites/aura quando
`sid` o `aura.token` non valgono piu' (redirect? errore strutturato?) non e'
stata mai vista su una cattura reale. L'integrazione non ci arriva da sola
perche' fa un login nuovo a ogni giro, quindi serve provocarla a mano.

Cosa fa: login (come verify_areti_login.py), poi una chiamata leggera
(getConfigurations sul tuo POD) a intervalli crescenti dal login, SENZA
fare altro nel frattempo. Per ogni chiamata registra lo stato HTTP, un
eventuale redirect, il tipo di contenuto, lo stato dell'azione Aura, un
estratto del corpo (con POD ed email oscurati) e se i cookie `sid` e del
token sono ancora nella sessione. Alla prima chiamata che fallisce prova una
volta a rileggere la home (/s/) e a ripetere la chiamata, per capire se
basta un token fresco o serve un login nuovo, poi si ferma.

Attenzione: ogni chiamata potrebbe rinnovare la scadenza per inattivita' del
server. Per misurare la durata di una sessione *inattiva* senza questo
effetto, usa un solo intervallo lungo, per esempio `--intervalli 60`. Per
vedere la scadenza anche solo nei primi minuti, lascia quelli di default.

Uso:
    pip install requests
    python3 scripts/verify_areti_sessione.py
    python3 scripts/verify_areti_sessione.py --intervalli 1,10,30,60

Gli intervalli sono minuti dal login. Con quelli di default (1, 2, 5, 10, 15,
30, 45, 60) lo script dura un'ora: lascialo girare, non serve toccarlo.
Ctrl+C in qualsiasi momento stampa e salva quanto raccolto fino a li'.

Scrive `areti_sessione_report.json` nella cartella corrente. Non contiene
password, token, valori dei cookie, email o codice POD; contiene pero'
estratti di risposte del portale, quindi guardalo prima di condividerlo.
La password viene letta con getpass (non resta nella history).
"""
from __future__ import annotations

import argparse
import getpass
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Manca 'requests': installalo con  pip install requests")

from verify_areti_login import (
    CA_BUNDLE,
    AretiAuthError,
    carica_contesto_aura,
    invia_apex,
    login,
)

INTERVALLI_DEFAULT_MINUTI = (1, 2, 5, 10, 15, 30, 45, 60)
ESTRATTO_MAX = 300
FILE_REPORT = Path("areti_sessione_report.json")


def _oscura(testo: str, *segreti: str) -> str:
    for segreto in segreti:
        if segreto:
            testo = testo.replace(segreto, "***")
    return testo


def _cookie_presenti(sess: requests.Session, nome_cookie_token: str | None) -> dict:
    nomi = {c.name for c in sess.cookies}
    return {
        "sid": "sid" in nomi,
        "cookie_token": (nome_cookie_token in nomi) if nome_cookie_token else None,
    }


def _nome_cookie_token(sess: requests.Session, token: str) -> str | None:
    for cookie in sess.cookies:
        if cookie.value == token:
            return cookie.name
    return None


def sonda(
    sess: requests.Session, contesto: dict, pod: str, segreti: tuple[str, ...]
) -> dict:
    """Una chiamata getConfigurations senza alcun controllo: registra com'e'
    andata, qualunque cosa risponda il portale."""
    inizio = time.monotonic()
    esito: dict = {"ora": datetime.now().isoformat(timespec="seconds")}
    try:
        r = invia_apex(
            sess, contesto, "ARIA_DatiDiMisuraController", "getConfigurations",
            {"podName": pod}, allow_redirects=False,
        )
    except requests.RequestException as exc:
        esito.update({"esito": "errore_di_rete", "dettaglio": type(exc).__name__})
        return esito
    esito["durata_s"] = round(time.monotonic() - inizio, 2)
    esito["http"] = r.status_code
    esito["content_type"] = r.headers.get("Content-Type")
    if "Location" in r.headers:
        esito["location"] = _oscura(r.headers["Location"][:200], *segreti)

    try:
        corpo = r.json()
    except ValueError:
        esito["json"] = False
        esito["estratto"] = _oscura(re.sub(r"\s+", " ", r.text)[:ESTRATTO_MAX], *segreti)
        esito["esito"] = "non_json"
        return esito

    esito["json"] = True
    if isinstance(corpo, dict):
        esito["chiavi"] = sorted(corpo)[:10]
        evento = corpo.get("event")
        if isinstance(evento, dict):
            esito["evento"] = evento.get("descriptor")
        azioni = corpo.get("actions")
        if azioni:
            azione = azioni[0]
            esito["stato_azione"] = azione.get("state")
            if azione.get("error"):
                esito["errore_azione"] = _oscura(
                    json.dumps(azione["error"], ensure_ascii=False)[:ESTRATTO_MAX], *segreti
                )
            if azione.get("state") == "SUCCESS":
                esito["esito"] = "ok"
                return esito
    if "estratto" not in esito:
        esito["estratto"] = _oscura(json.dumps(corpo, ensure_ascii=False)[:ESTRATTO_MAX], *segreti)
    esito["esito"] = "fallita"
    return esito


def _stampa(minuti: float, esito: dict, cookie: dict) -> None:
    dettagli = [f"HTTP {esito.get('http', '-')}", esito.get("esito", "?")]
    for chiave in ("stato_azione", "evento", "location"):
        if esito.get(chiave):
            dettagli.append(f"{chiave}={esito[chiave]}")
    dettagli.append(f"sid={'si' if cookie['sid'] else 'NO'}")
    dettagli.append(f"cookie_token={cookie['cookie_token']}")
    print(f"  dopo {minuti:g} min: " + ", ".join(dettagli))
    if esito.get("esito") not in ("ok", None):
        for chiave in ("errore_azione", "estratto", "dettaglio"):
            if esito.get(chiave):
                print(f"      {chiave}: {esito[chiave]}")


def _salva(report: dict) -> None:
    FILE_REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nReport salvato in {FILE_REPORT.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--intervalli",
        default=",".join(str(m) for m in INTERVALLI_DEFAULT_MINUTI),
        help="minuti dal login a cui provare, separati da virgola (default: %(default)s)",
    )
    argomenti = parser.parse_args()
    try:
        intervalli = sorted({float(x) for x in argomenti.intervalli.split(",") if x.strip()})
    except ValueError:
        sys.exit("--intervalli deve essere un elenco di numeri separati da virgola")
    if not intervalli or intervalli[0] <= 0:
        sys.exit("--intervalli deve contenere almeno un valore maggiore di zero")

    email = input("Email Areti: ").strip()
    password = getpass.getpass("Password (non visibile mentre digiti): ")
    pod = input("POD da usare per la chiamata di prova: ").strip()
    if not pod:
        sys.exit("Serve un POD associato all'account.")
    segreti = (email, pod)

    sess = requests.Session()
    sess.verify = CA_BUNDLE
    try:
        login(sess, email, password)
        contesto = carica_contesto_aura(sess)
    except AretiAuthError as exc:
        sys.exit(f"\nERRORE: {exc}")
    inizio_login = time.monotonic()
    nome_cookie_token = _nome_cookie_token(sess, contesto["token"])

    report: dict = {
        "avviato": datetime.now().isoformat(timespec="seconds"),
        "intervalli_minuti": intervalli,
        "prove": [],
        "recupero": None,
    }
    print(f"\nLogin riuscito. Prove a {', '.join(f'{m:g}' for m in intervalli)} minuti dal login.")
    print("Lascia girare lo script: non serve fare altro. Ctrl+C per fermarlo.\n")

    try:
        for minuti in intervalli:
            attesa = minuti * 60 - (time.monotonic() - inizio_login)
            if attesa > 0:
                time.sleep(attesa)
            esito = sonda(sess, contesto, pod, segreti)
            cookie = _cookie_presenti(sess, nome_cookie_token)
            esito["minuti_dal_login"] = minuti
            esito["cookie"] = cookie
            report["prove"].append(esito)
            _stampa(minuti, esito, cookie)
            if esito.get("esito") != "ok":
                print("\nLa sessione non risponde piu' come prima. Provo a rileggere la home...")
                try:
                    nuovo_contesto = carica_contesto_aura(sess)
                except Exception as exc:  # noqa: BLE001 - e' proprio l'esito da registrare
                    report["recupero"] = {"home_riletta": False, "dettaglio": str(exc)[:ESTRATTO_MAX]}
                    print(f"  Rilettura della home fallita: {exc}")
                else:
                    recupero = sonda(sess, nuovo_contesto, pod, segreti)
                    report["recupero"] = {"home_riletta": True, **recupero}
                    print(
                        "  Dopo la rilettura della home la chiamata e': "
                        f"{recupero.get('esito')} (HTTP {recupero.get('http', '-')})"
                    )
                break
    except KeyboardInterrupt:
        print("\nInterrotto: salvo quanto raccolto fin qui.")

    ok = [p for p in report["prove"] if p.get("esito") == "ok"]
    print("\n=== RIEPILOGO ===")
    if ok:
        print(f"Sessione ancora valida fino a {max(p['minuti_dal_login'] for p in ok):g} min dal login.")
    falliti = [p for p in report["prove"] if p.get("esito") != "ok"]
    if falliti:
        print(f"Prima risposta diversa dal normale: dopo {falliti[0]['minuti_dal_login']:g} min.")
    elif report["prove"]:
        print("Nessuna scadenza osservata negli intervalli provati.")
    report["terminato"] = datetime.now().isoformat(timespec="seconds")
    _salva(report)


if __name__ == "__main__":
    main()
