"""Testa da terminale il login SET Distribuzione e il recupero della curva
oraria, senza Home Assistant nel mezzo.

Importa direttamente auth.py/api.py/const.py di
custom_components/contatore_letture/distributors/setdistribuzione/ (che
non dipendono da Home Assistant), bypassando gli __init__.py della
gerarchia - stesso principio di verify_edistribuzione_login.py: verifica
il codice vero dell'integrazione, non una sua copia.

Catena: login Azure AD B2C (authorize -> SelfAsserted -> confirmed ->
token) -> forniture dell'account -> ore di un giorno con i quarti d'ora.

L'output è pensato per poter essere incollato in una issue: POD e
contratto compaiono mascherati, codice fiscale e nomi mai. Controllalo
comunque prima di condividerlo.

Uso:
    pip install aiohttp
    python3 scripts/verify_setdistribuzione_login.py

La password viene letta con getpass (non appare a schermo, non resta
nella history del terminale).
"""
from __future__ import annotations

import asyncio
import getpass
import importlib.util
import sys
import types
from datetime import date, timedelta
from pathlib import Path

try:
    import aiohttp
except ImportError:
    sys.exit("Manca 'aiohttp': installalo con  pip install aiohttp")

SET_DIR = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "contatore_letture"
    / "distributors"
    / "setdistribuzione"
)


def _load_modules():
    pkg_name = "setdistribuzione_script"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(SET_DIR)]
    sys.modules[pkg_name] = pkg

    def _load(modname: str):
        spec = importlib.util.spec_from_file_location(f"{pkg_name}.{modname}", SET_DIR / f"{modname}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg_name}.{modname}"] = mod
        spec.loader.exec_module(mod)
        return mod

    _load("const")
    return _load("auth"), _load("api")


auth, api = _load_modules()


def _maschera(valore: str) -> str:
    return valore[:6] + "*" * max(len(valore) - 9, 0) + valore[-3:] if len(valore) > 9 else "***"


async def main() -> None:
    email = input("Email mySET: ").strip()
    password = getpass.getpass("Password: ")
    testo = input("Giorno da provare (AAAA-MM-GG, invio = ieri): ").strip()
    giorno = date.fromisoformat(testo) if testo else date.today() - timedelta(days=1)

    async with aiohttp.ClientSession(cookie_jar=auth.nuovo_cookie_jar()) as session:
        print("1/3 login B2C...")
        try:
            token = await auth.SetDistribuzioneAuthClient(session).async_login(email, password)
        except auth.SetDistribuzioneInvalidCredentials as err:
            sys.exit(f"Credenziali rifiutate: {err}")
        except auth.SetDistribuzioneAuthError as err:
            sys.exit(f"Login fallito: {err}")
        print("    ok")

        client = api.SetDistribuzioneApiClient(session, token)
        print("2/3 forniture dell'account...")
        pods = await client.async_get_pod_account()
        if not pods:
            sys.exit("Nessuna fornitura elettrica sull'account (profilo Prospect?).")
        for p in pods:
            print(f"    POD {_maschera(p['pod'])}  profilo {p['profile']}  "
                  f"contratto {_maschera(p['contract_id'])}")

        print(f"3/3 ore del {giorno}...")
        for p in pods:
            ore = await client.async_get_consumi_giorno(
                p["fiscal_code"], p["profile"], p["contract_id"], p["pod"], giorno
            )
            if not ore:
                print(f"    POD {_maschera(p['pod'])}: nessun dato per il {giorno}")
                continue
            totale = sum(float(o.get("total") or 0) for o in ore)
            quarti = sum(1 for o in ore if isinstance(o.get("quarters"), list))
            stimate = sum(1 for o in ore if o.get("estimated"))
            print(f"    POD {_maschera(p['pod'])}: {len(ore)} ore ({quarti} con i quarti d'ora, "
                  f"{stimate} stimate), totale {totale:.3f} kWh")
            print(f"    ore presenti: {[o.get('hour') for o in ore]}")


if __name__ == "__main__":
    asyncio.run(main())
