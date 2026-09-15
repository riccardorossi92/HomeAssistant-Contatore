#!/usr/bin/env python3
"""Verifica il login AcegasApsAmga (Azure AD B2C) e prova gli endpoint energia.

Vedi documentation/acegasapsamga-protocol.md per il contesto. Chiede
utenza e password (la password non viene stampata né salvata) e fa solo
richieste dirette al portale servizionline.acegasapsamga.it, con lo
stesso traffico che farebbe un browser durante il login.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import json
import re
import secrets
import sys
from urllib.parse import parse_qs, urlencode, urlparse

import requests

LOGIN_HOST = "https://login.acegasapsamga.it"
PORTALE = "https://servizionline.acegasapsamga.it"
TENANT = "myheraapp.onmicrosoft.com"
POLICY = "B2C_1A_SignIn_Web"
CLIENT_ID = "40c94bb1-2d83-4ccc-8c72-fde8ad15ed24"
REDIRECT_URI = f"{PORTALE}/auth/acegas/login"
SCOPE = (
    "openid offline_access "
    f"https://{TENANT}/{CLIENT_ID}/read profile"
)

POLICY_BASE = f"{LOGIN_HOST}/{TENANT}/{POLICY}"

HEADERS_BROWSER = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
}


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_pkce() -> tuple[str, str]:
    verifier = b64url(secrets.token_bytes(32))
    challenge = b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def fail(msg: str) -> None:
    print(f"ERRORE: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    username = input("Email/utenza AcegasApsAmga: ").strip()
    password = getpass.getpass("Password: ")

    session = requests.Session()
    session.headers.update(HEADERS_BROWSER)

    verifier, challenge = make_pkce()
    nonce = secrets.token_hex(16)
    state = b64url(secrets.token_bytes(16))

    authorize_params = {
        "client_id": CLIENT_ID,
        "scope": SCOPE,
        "redirect_uri": REDIRECT_URI,
        "response_mode": "fragment",
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
        "nonce": nonce,
        "state": state,
        "referrer": "acegas",
        "env": "prod",
    }

    print("1/6 GET authorize...")
    r = session.get(f"{POLICY_BASE}/oauth2/v2.0/authorize", params=authorize_params)
    if r.status_code != 200:
        fail(f"authorize ha risposto {r.status_code}")

    m = re.search(r"var SETTINGS = (\{.*?\});", r.text, re.S)
    if not m:
        fail("non trovo il blob SETTINGS nella pagina di login (formato cambiato?)")
    settings = json.loads(m.group(1))
    tx = settings["transId"]
    csrf = settings["csrf"]

    print("2/6 POST SelfAsserted (credenziali)...")
    r = session.post(
        f"{POLICY_BASE}/SelfAsserted",
        params={"tx": tx, "p": POLICY},
        headers={
            "X-CSRF-TOKEN": csrf,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
        data={
            "request_type": "RESPONSE",
            "signInName": username,
            "password": password,
        },
    )
    if r.status_code != 200:
        fail(f"login fallito ({r.status_code}): {r.text[:300]}")
    try:
        body = r.json()
    except ValueError:
        body = None
    if body is not None and str(body.get("status")) not in ("200",):
        fail(f"login rifiutato: {body}")

    print("3/6 GET confirmed (redirect con authorization code)...")
    r = session.get(
        f"{POLICY_BASE}/api/CombinedSigninAndSignup/confirmed",
        params={"rememberMe": "false", "csrf_token": csrf, "tx": tx, "p": POLICY},
        allow_redirects=False,
    )
    location = r.headers.get("Location")
    if r.status_code not in (302, 303) or not location:
        fail(f"confirmed non ha restituito un redirect ({r.status_code})")

    fragment = urlparse(location).fragment
    frag_params = parse_qs(fragment)
    code = frag_params.get("code", [None])[0]
    if not code:
        fail("nessun 'code' nel redirect finale (credenziali errate?)")

    print("4/6 POST token (scambio code -> access_token)...")
    r = session.post(
        f"{POLICY_BASE}/oauth2/v2.0/token",
        data={
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "code": code,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
        },
    )
    if r.status_code != 200:
        fail(f"token exchange fallito ({r.status_code}): {r.text[:300]}")
    access_token = r.json()["access_token"]

    print("5/6 POST /api/v1/user/cookie (sessione sul portale)...")
    r = session.post(f"{PORTALE}/api/v1/user/cookie", json={"token": access_token})
    if r.status_code != 200:
        fail(f"impossibile creare la sessione sul portale ({r.status_code})")

    print("6/6 GET /api/v1/user (anagrafica)...")
    r = session.get(f"{PORTALE}/api/v1/user")
    if r.status_code != 200:
        fail(f"/api/v1/user ha risposto {r.status_code}: {r.text[:300]}")
    user = r.json()
    print()
    print("Login OK. Anagrafica:")
    print(f"  nome: {user.get('firstName')} {user.get('lastName')}")
    print(f"  prospect: {user.get('prospect')}")
    print(f"  sfdcMigrated: {user.get('sfdcMigrated')}")

    print()
    print("Profili associati (/api/profile/list):")
    r = session.get(f"{PORTALE}/api/profile/list")
    profiles = r.json() if r.status_code == 200 else {"list": []}
    print(f"  {json.dumps(profiles, ensure_ascii=False)[:500]}")

    print()
    print("Contratti (/api/profile/prospect/contract/list):")
    r = session.get(f"{PORTALE}/api/profile/prospect/contract/list")
    contracts = r.json() if r.status_code == 200 else {"list": []}
    print(f"  {json.dumps(contracts, ensure_ascii=False)[:500]}")

    if not profiles.get("list") and not contracts.get("list"):
        print()
        print(
            "Nessun profilo/contratto associato a questo account: come per "
            "la cattura usata in documentation/acegasapsamga-protocol.md, "
            "non si puo' proseguire con la verifica di /api/energy/*."
        )
        return

    print()
    print("Provo /api/energy/pod/list...")
    r = session.get(f"{PORTALE}/api/energy/pod/list")
    print(f"  status: {r.status_code}")
    print(f"  body: {r.text[:1000]}")

    print()
    print(
        "Se /api/energy/pod/list ha restituito dei POD, allega l'output di "
        "questo script (rivedi prima cosa contiene: puo' includere il tuo "
        "codice fiscale e altri dati personali) a una issue, cosi' "
        "possiamo completare l'integrazione."
    )


if __name__ == "__main__":
    main()
