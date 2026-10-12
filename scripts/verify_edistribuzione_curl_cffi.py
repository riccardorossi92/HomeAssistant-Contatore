"""Variante di verify_edistribuzione_login.py che usa curl_cffi al posto di
aiohttp come trasporto HTTP, per capire se l'impronta TLS/HTTP2 di un browser
vero basta a superare l'antibot Imperva di private.e-distribuzione.it.

Il codice di login testato e' quello VERO dell'integrazione (auth.py/api.py,
caricati tramite verify_edistribuzione_login.py): cambia solo la sessione HTTP
che gli viene passata, sostituita da un adattatore che espone la stessa
interfaccia di aiohttp.ClientSession ma sotto usa curl_cffi con
impersonate=<browser>.

Due modalita':

  1) Sonda (consigliata come primo passo, NESSUNA credenziale, nessun OTP):
       python3 verify_edistribuzione_curl_cffi.py --sonda
     Fa una GET alla pagina di login e a /services/oauth2/authorize con
     aiohttp (riferimento) e con curl_cffi in varie impronte, e per ciascuna
     dice se Imperva ha risposto con la pagina antibot o con la pagina vera.

  2) Login completo (email/password -> OTP -> POD -> curva), identico a
     verify_edistribuzione_login.py ma via curl_cffi:
       python3 verify_edistribuzione_curl_cffi.py --impersonate chrome

Opzioni:
  --impersonate NOME   impronta curl_cffi (default: chrome). Da provare anche
                       safari, safari_ios, firefox.
  --mantieni-ua        NON sostituire lo User-Agent impostato da auth.py
                       (iPhone Safari). Di default viene rimosso e si usa
                       quello coerente con l'impronta scelta: un UA Safari
                       con un'impronta TLS Chrome e' esattamente il tipo di
                       incoerenza che Imperva nota.
  --consenti-fallback  permette ad auth.py di ripiegare su
                       edistribuzione.my.site.com se il dominio principale e'
                       bloccato. Di default e' disattivato, cosi' il test dice
                       se curl_cffi passa Imperva sul dominio PRINCIPALE.

Uso (dalla cartella scripts/ del repository, accanto a
verify_edistribuzione_login.py):
    python3 -m venv .venv && source .venv/bin/activate
    pip install curl_cffi aiohttp
    python3 verify_edistribuzione_curl_cffi.py --sonda
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json as json_lib
import logging
import sys
from pathlib import Path

try:
    import aiohttp
    from yarl import URL
except ImportError:
    sys.exit("Manca aiohttp: pip install aiohttp")

try:
    from curl_cffi.requests import AsyncSession
except ImportError:
    sys.exit("Manca curl_cffi: pip install curl_cffi")

_LOGGER = logging.getLogger("curl_cffi_shim")

SCRIPT_DIR = Path(__file__).resolve().parent
ORIGINALE = SCRIPT_DIR / "verify_edistribuzione_login.py"

HOST = "private.e-distribuzione.it"
URL_LOGIN = f"https://{HOST}/PortaleClienti/s/login/"
URL_AUTHORIZE = f"https://{HOST}/PortaleClienti/services/oauth2/authorize"

MARCATORI_ANTIBOT = ("pardon our interruption", "incapsula incident id")


# --- Adattatore aiohttp -> curl_cffi -----------------------------------------
# Implementa solo cio' che auth.py e api.py usano davvero: get/post
# (sia "await session.get(...)" sia "async with session.get(...) as resp"),
# resp.status/.headers/.url (yarl)/.text()/.json()/.raise_for_status()/.close().


class _Risposta:
    def __init__(self, r) -> None:
        self._r = r
        self.status = r.status_code
        self.headers = r.headers  # case-insensitive, .get() come aiohttp
        self.url = URL(str(r.url), encoded=True)

    async def text(self) -> str:
        return self._r.text

    async def json(self, content_type=None):  # noqa: ARG002 - firma aiohttp
        return json_lib.loads(self._r.text)

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                request_info=None,  # type: ignore[arg-type]
                history=(),
                status=self.status,
                message=f"HTTP {self.status} su {self.url}",
            )

    def close(self) -> None:
        pass

    def release(self) -> None:
        pass


class _Richiesta:
    """Come aiohttp._RequestContextManager: awaitable e async context manager."""

    def __init__(self, coro) -> None:
        self._coro = coro

    def __await__(self):
        return self._coro.__await__()

    async def __aenter__(self) -> _Risposta:
        return await self._coro

    async def __aexit__(self, *exc) -> bool:
        return False


class CurlCffiSession:
    """Sostituto minimo di aiohttp.ClientSession basato su curl_cffi."""

    def __init__(
        self,
        impersonate: str,
        mantieni_ua: bool,
        cookies: list[dict] | None = None,
        ua_forzato: str | None = None,
    ) -> None:
        self._impersonate = impersonate
        self._mantieni_ua = mantieni_ua
        self._ua_forzato = ua_forzato
        self._s = AsyncSession(impersonate=impersonate)
        for c in cookies or []:
            self._s.cookies.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))

    async def __aenter__(self) -> "CurlCffiSession":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        await self._s.close()

    def get(self, url, **kw) -> _Richiesta:
        return _Richiesta(self._request("GET", url, **kw))

    def post(self, url, **kw) -> _Richiesta:
        return _Richiesta(self._request("POST", url, **kw))

    async def _request(
        self,
        method: str,
        url,
        *,
        params=None,
        data=None,
        json=None,
        headers=None,
        allow_redirects: bool = True,
    ) -> _Risposta:
        headers = dict(headers or {})
        if not self._mantieni_ua:
            for k in list(headers):
                if k.lower() == "user-agent":
                    del headers[k]
        token_endpoint = str(url).split("?")[0].endswith("/services/oauth2/token")
        if token_endpoint and not any(k.lower() == "accept" for k in headers):
            # L'Accept di default dell'impronta Chrome include application/xml:
            # il token endpoint di Salesforce rispetta l'header e risponderebbe
            # in XML invece che in JSON (aiohttp mandava */*).
            headers["Accept"] = "application/json"
        if self._ua_forzato:
            # I cookie Imperva valgono solo insieme allo User-Agent del
            # browser che li ha ottenuti.
            headers["User-Agent"] = self._ua_forzato
        r = await self._s.request(
            method,
            str(url),
            params=params,
            data=data,
            json=json,
            headers=headers,
            allow_redirects=allow_redirects,
            # Niente ri-codifica dell'URL: i Location di Salesforce contengono
            # startURL gia' percent-encoded e vanno rimandati identici (stesso
            # motivo per cui auth.py segue i redirect a mano con encoded=True).
            quote=False,
        )
        risp = _Risposta(r)
        antibot = any(m in r.text.lower() for m in MARCATORI_ANTIBOT)
        _LOGGER.info(
            "%s %s%s -> %s%s%s",
            method,
            risp.url.host,
            risp.url.path,
            r.status_code,
            f" x-iinfo={r.headers.get('x-iinfo')}" if r.headers.get("x-iinfo") else "",
            "  <<< PAGINA ANTIBOT IMPERVA" if antibot else "",
        )
        if token_endpoint:
            corpo = r.text.lstrip()
            if corpo.startswith("{"):
                chiavi = sorted(json_lib.loads(corpo).keys())
                _LOGGER.info("  token endpoint: JSON con chiavi %s", chiavi)
            else:
                _LOGGER.warning(
                    "  token endpoint: risposta NON JSON (content-type %r, %d caratteri): %r",
                    r.headers.get("content-type"), len(r.text), r.text[:400],
                )
        return risp


# --- Modalita' sonda -----------------------------------------------------------


def _descrivi(status: int, testo: str, location: str | None, cookies: list[str]) -> str:
    basso = testo.lower()
    if any(m in basso for m in MARCATORI_ANTIBOT):
        esito = "BLOCCATO (pagina antibot)"
    elif status in (301, 302, 303) and location:
        esito = f"OK, redirect -> {location[:90]}"
    elif status == 200 and ("fwuid" in basso or "siteforce" in basso):
        esito = "OK (pagina di login Salesforce vera)"
    else:
        esito = f"incerto (HTTP {status}, {len(testo)} caratteri)"
    incap = [c for c in cookies if c.startswith(("visid_incap", "incap_ses", "nlbi", "reese84"))]
    return f"{esito}; cookie Imperva ricevuti: {incap or 'nessuno'}"


async def _sonda_aiohttp() -> None:
    print("\n[aiohttp - riferimento, come l'integrazione oggi]")
    ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15"
    async with aiohttp.ClientSession(headers={"User-Agent": ua}) as s:
        for nome, url, params in _richieste_sonda():
            try:
                async with s.get(url, params=params, allow_redirects=False) as r:
                    testo = await r.text(errors="replace")
                    cookies = [c.key for c in s.cookie_jar]
                    print(f"  {nome:10} HTTP {r.status}: {_descrivi(r.status, testo, r.headers.get('Location'), cookies)}")
            except Exception as exc:  # noqa: BLE001
                print(f"  {nome:10} ERRORE: {exc!r}")


async def _sonda_curl(impersonate: str) -> None:
    print(f"\n[curl_cffi impersonate={impersonate}]")
    try:
        s = AsyncSession(impersonate=impersonate)
    except Exception as exc:  # noqa: BLE001
        print(f"  impronta non disponibile in questa versione di curl_cffi: {exc}")
        return
    try:
        for nome, url, params in _richieste_sonda():
            try:
                r = await s.get(url, params=params, allow_redirects=False)
                cookies = list(s.cookies.keys())
                print(f"  {nome:10} HTTP {r.status_code}: {_descrivi(r.status_code, r.text, r.headers.get('Location'), cookies)}")
            except Exception as exc:  # noqa: BLE001
                print(f"  {nome:10} ERRORE: {exc!r}")
            await asyncio.sleep(1.5)  # niente raffiche
    finally:
        await s.close()


def _richieste_sonda():
    # Authorize con i soli parametri pubblici dell'app (nessun dato utente):
    # e' l'endpoint su cui il blocco e' stato osservato nei log (issue #8).
    params_auth = {
        "response_type": "code",
        "client_id": (
            "3MVG9Rd3qC6oMalUVUXDfNEIi52RXSZMsndjnAnbka4R4Amhq6QrTj2U2Zw0sjyqOlFViLC"
            ".cTpau8fPEBV0V"
        ),
        "redirect_uri": "eneldist://redirect",
        "display": "touch",
        "prompt": "login",
    }
    return [
        ("login", URL_LOGIN, None),
        ("authorize", URL_AUTHORIZE, params_auth),
    ]


async def sonda(impronte: list[str]) -> None:
    print(f"Sonda verso {HOST} (nessuna credenziale inviata)")
    await _sonda_aiohttp()
    for imp in impronte:
        await asyncio.sleep(2)
        await _sonda_curl(imp)
    print(
        "\nLettura: se aiohttp e' BLOCCATO e curl_cffi e' OK, l'impronta TLS "
        "basta -> prova il login completo con quell'impronta.\nSe anche "
        "curl_cffi e' BLOCCATO, Imperva vuole l'esecuzione del suo JavaScript "
        "(cookie reese84): serve un browser vero (Playwright) per ottenerlo."
    )


# --- Modalita' sonda con browser (Playwright -> cookie -> curl_cffi) -----------


async def _cookie_da_browser(headed: bool, attesa_s: int) -> tuple[list[dict], str]:
    """Apre la pagina di login in Chromium, lascia eseguire il JavaScript di
    Imperva e ritorna (cookie, user_agent) appena la pagina vera e' caricata."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        sys.exit("Manca playwright: pip install playwright && playwright install chromium")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=not headed,
            args=["--disable-blink-features=AutomationControlled"],
        )
        # In headless lo User-Agent contiene "HeadlessChrome": un'auto-denuncia.
        ua_base = await (await browser.new_page()).evaluate("navigator.userAgent")
        ctx = await browser.new_context(
            locale="it-IT", user_agent=ua_base.replace("HeadlessChrome", "Chrome")
        )
        page = await ctx.new_page()
        print(f"  Chromium {'visibile' if headed else 'headless'}: apro {URL_LOGIN}")
        await page.goto(URL_LOGIN, wait_until="domcontentloaded")

        superato = False
        for secondo in range(attesa_s):
            try:
                html = (await page.content()).lower()
            except Exception:  # noqa: BLE001 - la pagina si sta ricaricando
                print(f"  ~{secondo}s: la pagina si ricarica (tipico dopo la verifica Imperva)")
                await asyncio.sleep(1)
                continue
            nomi = {c["name"] for c in await ctx.cookies()}
            antibot = any(m in html for m in MARCATORI_ANTIBOT)
            if not antibot and ("siteforce" in html or "fwuid" in html):
                superato = True
                print(f"  pagina vera caricata dopo ~{secondo}s; reese84 presente: {'reese84' in nomi}")
                break
            await asyncio.sleep(1)
        if not superato:
            print(f"  dopo {attesa_s}s il browser vede ancora la pagina antibot (o una pagina inattesa)")
        try:
            print(f"  pagina finale: {page.url} - titolo {await page.title()!r}")
        except Exception:  # noqa: BLE001
            pass

        cookies = await ctx.cookies()
        ua = await page.evaluate("navigator.userAgent")
        await browser.close()
    return cookies, ua


async def sonda_browser(headed: bool, attesa_s: int, impersonate: str) -> None:
    print(f"Sonda ibrida verso {HOST}: browser per Imperva, poi curl_cffi (nessuna credenziale)\n")
    print("[1] Browser")
    cookies, ua = await _cookie_da_browser(headed, attesa_s)
    nomi = [c["name"] for c in cookies if c["name"].startswith(("visid_incap", "incap_ses", "nlbi", "reese84"))]
    print(f"  cookie Imperva ottenuti: {nomi or 'nessuno'}")
    print(f"  User-Agent del browser: {ua}")

    print(f"\n[2] curl_cffi impersonate={impersonate}, con i cookie e lo User-Agent del browser")
    s = AsyncSession(impersonate=impersonate)
    try:
        for c in cookies:
            s.cookies.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))
        for nome, url, params in _richieste_sonda():
            try:
                r = await s.get(url, params=params, headers={"User-Agent": ua}, allow_redirects=False)
                print(f"  {nome:10} HTTP {r.status_code}: {_descrivi(r.status_code, r.text, r.headers.get('Location'), list(s.cookies.keys()))}")
            except Exception as exc:  # noqa: BLE001
                # curl (16) = errore HTTP/2: tipicamente il server chiude la
                # connessione a un client che ha appena classificato come bot.
                print(f"  {nome:10} ERRORE di connessione: {exc}")
            await asyncio.sleep(1.5)
    finally:
        await s.close()

    print(
        "\nLettura:\n"
        " - [1] OK e [2] OK  -> l'ibrido funziona: un add-on con browser rinnova i cookie,\n"
        "   l'integrazione continua via HTTP. Prossimo passo: login completo con i cookie.\n"
        " - [1] OK e [2] BLOCCATO -> i cookie sono legati all'impronta del browser:\n"
        "   le richieste devono partire dal browser stesso (Playwright per tutto il flusso).\n"
        " - [1] BLOCCATO -> riprova con --headed (headless viene riconosciuto);\n"
        "   se blocca anche cosi', serve un browser piu' mimetico (Patchright/Camoufox)."
    )


# --- Modalita' login completo ---------------------------------------------------


def _carica_script_originale():
    if not ORIGINALE.exists():
        sys.exit(f"Non trovo {ORIGINALE}: metti questo file in scripts/ accanto a lui.")
    spec = importlib.util.spec_from_file_location("verify_edistribuzione_login", ORIGINALE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def login_completo(
    impersonate: str,
    mantieni_ua: bool,
    consenti_fallback: bool,
    cookie_da_browser: bool = False,
    headed: bool = False,
    attesa_s: int = 30,
) -> None:
    cookies: list[dict] | None = None
    ua_browser: str | None = None
    if cookie_da_browser:
        print("[1] Ottengo i cookie Imperva dal browser")
        cookies, ua_browser = await _cookie_da_browser(headed, attesa_s)
        if not any(c["name"] == "reese84" for c in cookies):
            sys.exit("Il browser non ha ottenuto reese84: riprova con --headed.")
        print(f"  ok, {len(cookies)} cookie; procedo con il login via curl_cffi\n")

    orig = _carica_script_originale()

    # 1) Dopo il caricamento di auth.py, se richiesto disattiva il ripiego
    #    sull'host diretto: con SF_HOST_DIRETTO == principale, il primo blocco
    #    antibot viene rilanciato invece di riprovare su my.site.com.
    carica_originale = orig._load_edistribuzione_modules

    def carica_e_configura():
        auth, api = carica_originale()
        if not consenti_fallback:
            auth.SF_HOST_DIRETTO = auth.SF_HOST_PRINCIPALE
            print(f"(fallback su edistribuzione.my.site.com DISATTIVATO: si testa solo {HOST})")
        return auth, api

    orig._load_edistribuzione_modules = carica_e_configura

    # 2) Il main originale crea la sessione con aiohttp.ClientSession():
    #    la sostituiamo con l'adattatore curl_cffi per tutta la sua durata.
    originale_cs = aiohttp.ClientSession
    aiohttp.ClientSession = lambda *a, **kw: CurlCffiSession(  # type: ignore[assignment]
        impersonate, mantieni_ua, cookies=cookies, ua_forzato=ua_browser
    )
    try:
        print(f"Trasporto HTTP: curl_cffi impersonate={impersonate}"
              f"{' (UA di auth.py mantenuto)' if mantieni_ua else ' (UA coerente con l impronta)'}")
        await orig.main()
    finally:
        aiohttp.ClientSession = originale_cs  # type: ignore[assignment]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sonda", action="store_true", help="solo test antibot, senza credenziali")
    p.add_argument("--impersonate", default="chrome")
    p.add_argument("--mantieni-ua", action="store_true")
    p.add_argument("--consenti-fallback", action="store_true")
    p.add_argument("--sonda-browser", action="store_true",
                   help="cookie Imperva da Chromium (Playwright), poi richieste curl_cffi")
    p.add_argument("--cookie-da-browser", action="store_true",
                   help="login completo: prima prende i cookie Imperva da Chromium")
    p.add_argument("--headed", action="store_true", help="browser visibile")
    p.add_argument("--attesa", type=int, default=30, help="con --sonda-browser: secondi massimi di attesa")
    args = p.parse_args()

    if args.sonda_browser:
        asyncio.run(sonda_browser(args.headed, args.attesa, args.impersonate))
        return

    if args.sonda:
        logging.basicConfig(level=logging.WARNING)
        impronte = [args.impersonate] + [i for i in ("safari", "safari_ios", "firefox") if i != args.impersonate]
        asyncio.run(sonda(impronte))
        return

    # Il main originale imposta DEBUG su tutto; qui teniamo visibili le righe
    # dell'adattatore (una per richiesta, con l'esito antibot).
    asyncio.run(login_completo(
        args.impersonate, args.mantieni_ua, args.consenti_fallback,
        cookie_da_browser=args.cookie_da_browser, headed=args.headed, attesa_s=args.attesa,
    ))


if __name__ == "__main__":
    main()
