"""Testa il vero codice di login E-Distribuzione (auth.py + api.py) da
terminale, senza Home Assistant nel mezzo - molto più veloce che passare
dalla UI ad ogni tentativo.

Copre l'intera catena: email/password -> OTP -> recupero POD
(async_get_supplies), lo stesso percorso del config flow reale.

Dopo un login riuscito salva il refresh_token in
edistribuzione_refresh_token.txt (file locale, escluso da git): al lancio
successivo lo script offre di testare SOLO il refresh
(async_refresh_access_token), senza rifare email/password/OTP - il modo
pratico per verificare periodicamente se il refresh continua a
funzionare nel tempo, cosa non testabile in un colpo solo come il login.

Importa auth.py e api.py DIRETTAMENTE (bypassando i vari __init__.py della
gerarchia distributors/edistribuzione/, che importano
homeassistant.config_entries/core - non installati qui e non necessari:
né auth.py né api.py dipendono da Home Assistant, solo da aiohttp e
libreria standard).

Uso:
    pip install aiohttp
    python3 verify_edistribuzione_login.py

La password viene letta con getpass (non appare a schermo, non resta
nella history del terminale).
"""
from __future__ import annotations

import asyncio
import getpass
import importlib.util
import json
import logging
import sys
import types
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
EDISTRIBUZIONE_DIR = (
    REPO_ROOT
    / "custom_components"
    / "contatore_letture"
    / "distributors"
    / "edistribuzione"
)


def _load_edistribuzione_modules():
    """Carica const.py, auth.py e api.py come un mini-pacchetto isolato,
    senza eseguire i vari __init__.py reali (che richiedono homeassistant).
    Ritorna (auth, api)."""
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
    auth = _load("auth", "auth.py")
    api = _load("api", "api.py")
    return auth, api


REFRESH_TOKEN_FILE = Path("edistribuzione_refresh_token.txt")


async def _test_refresh_soltanto(auth) -> None:
    """Testa async_refresh_access_token con il token salvato da un login
    precedente, senza rifare email/password/OTP - il modo rapido per
    verificare periodicamente se il refresh continua a funzionare nel
    tempo (obiettivo: capire se/quando scade, non testabile in un colpo
    solo come il login)."""
    refresh_token_salvato = REFRESH_TOKEN_FILE.read_text(encoding="utf-8").strip()

    import aiohttp

    async with aiohttp.ClientSession() as session:
        client = auth.EdistribuzioneAuthClient(session)
        print(f"\n--- Test refresh con il token salvato in {REFRESH_TOKEN_FILE} ---")
        try:
            tokens = await client.async_refresh_access_token(refresh_token_salvato)
        except auth.EdistribuzioneAuthError as exc:
            print(f"\nREFRESH FALLITO: {exc}")
            print(
                "Se il messaggio parla di token non valido/scaduto/revocato, il "
                "refresh_token non è più utilizzabile: serve rifare login+OTP da capo "
                "(rilancia lo script e rispondi 'n' alla prossima domanda)."
            )
            return
        except Exception:
            print("Errore IMPREVISTO durante il refresh (traceback completo sotto):\n")
            raise

        print("\n=== REFRESH RIUSCITO ===")
        print(f"access_token  (primi 20 char): {tokens.access_token[:20]}...")
        if tokens.refresh_token == refresh_token_salvato:
            print("refresh_token: invariato rispetto a quello salvato")
        else:
            print(
                f"refresh_token: CAMBIATO (primi 20 char nuovo: {tokens.refresh_token[:20]}...) "
                "- salvo il nuovo al posto del vecchio"
            )
            REFRESH_TOKEN_FILE.write_text(tokens.refresh_token, encoding="utf-8")


async def main() -> None:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(levelname)s %(name)s: %(message)s",
    )
    auth, api = _load_edistribuzione_modules()

    try:
        import aiohttp
    except ImportError:
        sys.exit("Manca aiohttp: pip install aiohttp")

    if REFRESH_TOKEN_FILE.exists():
        scelta = input(
            f"\nTrovato un refresh_token salvato in {REFRESH_TOKEN_FILE} (da un login "
            "precedente). Vuoi testare SOLO quello (niente email/password/OTP)? [S/n]: "
        ).strip().lower()
        if scelta in ("", "s", "si", "sì", "y", "yes"):
            await _test_refresh_soltanto(auth)
            return
        print("Ok, procedo con login completo (email/password/OTP).\n")

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

        REFRESH_TOKEN_FILE.write_text(tokens.refresh_token, encoding="utf-8")
        print(
            f"\nrefresh_token salvato in {REFRESH_TOKEN_FILE.resolve()} - la prossima "
            "volta puoi testarlo direttamente senza rifare login+OTP (è un file locale, "
            "già escluso da git: non va mai committato)."
        )

        print("\n--- Recupero POD (async_get_supplies) ---")
        api_client = api.EdistribuzioneApiClient(session, tokens.access_token)
        try:
            pods = await api_client.async_get_supplies()
        except Exception:
            print("Errore IMPREVISTO nel recupero dei POD (traceback completo sotto):\n")
            raise

        print(f"\n=== POD TROVATI: {len(pods)} ===")
        for i, p in enumerate(pods, start=1):
            indirizzo = (
                f"{p.get('PointOfMeasureStreetPrefix', '')} "
                f"{p.get('PointOfMeasureStreet', '')} "
                f"{p.get('PointOfMeasureStreetNumber', '')}, "
                f"{p.get('PointOfMeasureMunicipality', '')} "
                f"({p.get('PointOfMeasureProvince', '')})"
            ).strip()
            print(f"  [{i}] {p.get('IdPod')} - {indirizzo}")

        if not pods:
            print("Nessun POD trovato, mi fermo qui.")
            return

        scelta = input(
            f"\nQuale POD vuoi interrogare? [1-{len(pods)}, invio per saltare]: "
        ).strip()
        if not scelta:
            return
        try:
            pod_scelto = pods[int(scelta) - 1]["IdPod"]
        except (ValueError, IndexError):
            print("Scelta non valida, mi fermo qui.")
            return

        default_giorno = date.today() - timedelta(days=7)
        giorno_input = input(
            f"Giorno di inizio da interrogare (YYYY-MM-DD) [invio per {default_giorno.isoformat()}]: "
        ).strip()
        try:
            giorno_da = date.fromisoformat(giorno_input) if giorno_input else default_giorno
        except ValueError:
            print(f"Data non valida, uso il default {default_giorno.isoformat()}.")
            giorno_da = default_giorno

        giorno_a_input = input(
            f"Giorno di fine (YYYY-MM-DD) [invio per usare solo {giorno_da.isoformat()}, "
            "un singolo giorno - metti una data successiva per testare se l'API supporta "
            "davvero un intervallo]: "
        ).strip()
        try:
            giorno_a = date.fromisoformat(giorno_a_input) if giorno_a_input else giorno_da
        except ValueError:
            print(f"Data non valida, uso solo {giorno_da.isoformat()}.")
            giorno_a = giorno_da

        print(f"\n--- Recupero curva di carico per {pod_scelto}: {giorno_da} - {giorno_a} ---")
        try:
            curva = await api_client.async_get_daily_load_profile(pod_scelto, giorno_da, giorno_a)
        except Exception:
            print("Errore IMPREVISTO nel recupero della curva (traceback completo sotto):\n")
            raise

        print(f"\n=== {len(curva)} elemento/i ricevuto/i nella lista 'data' ===")
        if giorno_a != giorno_da:
            giorni_attesi = (giorno_a - giorno_da).days + 1
            date_ricevute = sorted({g.get("readings", {}).get("sampleDate") for g in curva})
            print(f"Giorni richiesti nell'intervallo: {giorni_attesi}")
            print(f"Valori distinti di 'sampleDate' nella risposta: {date_ricevute}")
            if len(curva) >= giorni_attesi and len(date_ricevute) >= giorni_attesi:
                print(
                    "=> Sembra che l'endpoint supporti davvero un intervallo multi-giorno "
                    "in un'unica risposta."
                )
            else:
                print(
                    "=> L'endpoint NON sembra restituire l'intero intervallo richiesto "
                    "(pochi elementi/date rispetto ai giorni richiesti) - probabile che "
                    "tronchi o ignori rangeDateTo diverso da rangeDateFrom."
                )

        if curva:
            print("\nPrimo punto (struttura completa):")
            print(json.dumps(curva[0], indent=2, ensure_ascii=False))

        debug_path = Path("daily_load_profile_debug.json")
        debug_path.write_text(
            json.dumps(curva, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nRisposta completa salvata in {debug_path.resolve()}")

        totale_curva = sum(
            float(c.get("val", 0))
            for g in curva
            for c in g.get("readings", {}).get("sampleValues", [])
        )
        print(f"\nTotale energia nella curva per il periodo (assumendo 'val' = kWh): {totale_curva:.3f} kWh")

        conferma = input(
            "\nVuoi confrontarlo con la lettura ufficiale per lo stesso periodo "
            "(async_get_reading), per verificare se 'val' è davvero kWh e non kW? [S/n]: "
        ).strip().lower()
        if conferma in ("", "s", "si", "sì", "y", "yes"):
            print(f"\n--- Recupero lettura ufficiale per {pod_scelto}: {giorno_da} - {giorno_a} ---")
            try:
                letture = await api_client.async_get_reading(pod_scelto, giorno_da, giorno_a)
            except Exception:
                print("Errore IMPREVISTO nel recupero della lettura (traceback completo sotto):\n")
                raise

            debug_letture_path = Path("reading_debug.json")
            debug_letture_path.write_text(
                json.dumps(letture, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(f"\n=== {len(letture)} lettura/e ricevuta/e - salvate in {debug_letture_path.resolve()} ===")

            if len(letture) < 2:
                print(
                    "Meno di 2 letture nel periodo: non posso calcolare un delta "
                    "automaticamente (servono almeno una lettura prima e una dopo il "
                    "periodo). Guarda il file salvato per capire la struttura reale."
                )
            else:
                print("\nPrima lettura (struttura completa):")
                print(json.dumps(letture[0], indent=2, ensure_ascii=False))
                print("\nUltima lettura (struttura completa):")
                print(json.dumps(letture[-1], indent=2, ensure_ascii=False))

                def _somma_ea(lettura: dict) -> float:
                    """Somma i valori 'EA' (energia attiva) di tutte le fasce
                    (publishedSlots) in una singola lettura cumulativa.

                    Le fasce non attive sul contratto (es. T4-T6 su un
                    contratto monorario/bioraro) hanno "value": null nel
                    JSON, non la chiave assente - .get("value", 0) non
                    sostituisce 0 in quel caso (il default si applica solo
                    se la chiave manca, non se vale null), va gestito a
                    parte."""
                    return sum(
                        float(slot.get("value") or 0)
                        for slot in lettura.get("publishedSlots", [])
                        if slot.get("magnitude") == "EA"
                    )

                prima = _somma_ea(letture[0])
                ultima = _somma_ea(letture[-1])
                delta_ufficiale = ultima - prima

                print(f"\nSomma EA (tutte le fasce) prima lettura: {prima:.3f}")
                print(f"Somma EA (tutte le fasce) ultima lettura: {ultima:.3f}")
                print(f"Delta (consumo ufficiale nel periodo): {delta_ufficiale:.3f} kWh")
                print(f"Totale curva di carico per lo stesso periodo: {totale_curva:.3f} kWh")

                if delta_ufficiale > 0:
                    rapporto = totale_curva / delta_ufficiale
                    print(f"\nRapporto curva/ufficiale: {rapporto:.3f}")
                    if 0.95 <= rapporto <= 1.05:
                        print(
                            "=> Combaciano (entro il 5%): l'assunzione 'val = kWh per "
                            "intervallo' sembra confermata."
                        )
                    else:
                        print(
                            "=> NON combaciano: 'val' potrebbe non essere kWh per "
                            "intervallo come assunto in statistics.py. Occhio in "
                            "particolare a un rapporto vicino a 0.25 o 4 (confusione "
                            "kWh/kW su intervalli da 15 minuti)."
                        )
                else:
                    print(
                        "\nDelta ufficiale a zero o negativo: non riesco a calcolare un "
                        "rapporto sensato. Controlla reading_debug.json a mano - "
                        "probabile che il periodo scelto non sia coperto da letture "
                        "ufficiali pubblicate, o che la struttura sia diversa da quella "
                        "assunta qui."
                    )


if __name__ == "__main__":
    asyncio.run(main())
