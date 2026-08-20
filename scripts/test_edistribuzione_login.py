"""Testa il vero codice di login E-Distribuzione (auth.py) da terminale,
senza Home Assistant nel mezzo - molto più veloce che passare dalla UI ad
ogni tentativo.

Importa auth.py DIRETTAMENTE (bypassando i vari __init__.py della
gerarchia distributors/edistribuzione/, che importano
homeassistant.config_entries/core - non installati qui e non necessari:
auth.py di per sé non dipende da Home Assistant, solo da aiohttp e
libreria standard).

Uso:
    pip install aiohttp
    python3 test_edistribuzione_login.py

La password viene letta con getpass (non appare a schermo, non resta
nella history del terminale).
"""
from __future__ import annotations

import asyncio
import getpass
import importlib.util
import logging
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
EDISTRIBUZIONE_DIR = (
    REPO_ROOT
    / "custom_components"
    / "contatore_letture"
    / "distributors"
    / "edistribuzione"
)


def _load_auth_module():
    """Carica const.py e auth.py come un mini-pacchetto isolato, senza
    eseguire i vari __init__.py reali (che richiedono homeassistant)."""
    if not EDISTRIBUZIONE_DIR.exists():
        sys.exit(
            f"Non trovo {EDISTRIBUZIONE_DIR} - lancia questo script dalla "
            "cartella 'scripts/' dentro il repository, con la struttura "
            "custom_components/contatore_letture/... accanto."
        )

    pkg_name = "edistribuzione_standalone"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(EDISTRIBUZIONE_DIR)]
    sys.modules[pkg_name] = pkg

    def _load(modname: str, filename: str):
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{modname}", EDISTRIBUZIONE_DIR / filename
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg_name}.{modname}"] = mod
        spec.loader.exec_module(mod)
        return mod

    _load("const", "const.py")
    return _load("auth", "auth.py")


async def main() -> None:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(levelname)s %(name)s: %(message)s",
    )
    auth = _load_auth_module()

    try:
        import aiohttp
    except ImportError:
        sys.exit("Manca aiohttp: pip install aiohttp")

    email = input("Email E-Distribuzione: ").strip()
    password = getpass.getpass("Password (non visibile mentre digiti): ")

    async with aiohttp.ClientSession() as session:
        client = auth.EdistribuzioneAuthClient(session)

        print("\n--- Invio email/password ---")
        try:
            await client.async_begin_login(email, password)
        except auth.EdistribuzioneInvalidCredentials as exc:
            print(f"Credenziali rifiutate: {exc}")
            return
        except auth.EdistribuzioneParsingError as exc:
            print(f"Errore di parsing (pagina di login cambiata?): {exc}")
            return
        except Exception:
            print("Errore IMPREVISTO (traceback completo sotto):\n")
            raise

        print("OK: email/password accettate.")

        print("\n--- Invio codice OTP ---")
        otp = input("Codice OTP ricevuto via email o SMS: ").strip()
        try:
            tokens = await client.async_submit_otp(otp)
        except auth.EdistribuzioneInvalidOtp as exc:
            print(f"OTP rifiutato: {exc}")
            return
        except auth.EdistribuzioneParsingError as exc:
            print(f"Errore di parsing (pagina OTP cambiata?): {exc}")
            return
        except Exception:
            print("Errore IMPREVISTO (traceback completo sotto):\n")
            raise

        print("\n=== LOGIN COMPLETO RIUSCITO ===")
        print(f"access_token  (primi 20 char): {tokens.access_token[:20]}...")
        print(f"refresh_token (primi 20 char): {tokens.refresh_token[:20]}...")
        print(f"instance_url: {tokens.instance_url}")


if __name__ == "__main__":
    asyncio.run(main())
