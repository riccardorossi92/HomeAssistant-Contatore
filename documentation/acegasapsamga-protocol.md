# AcegasApsAmga — stato della ricerca (supporto non ancora implementato)

AcegasApsAmga (`servizionline.acegasapsamga.it`, gruppo Hera) **non è
ancora supportato**. Login e anagrafica sono verificati con dati reali;
gli **endpoint dei consumi sono stati individuati** nel bundle
dell'app (vedi [Endpoint energia — individuati, non ancora
provati](#endpoint-energia--individuati-non-ancora-provati-con-dati-reali)),
ma la cattura disponibile è di un account **senza alcun contratto
associato** ("prospect"), quindi non sappiamo se restituiscano vere
curve di carico del distributore.

> [!WARNING]
> AcegasApsAmga fa parte del gruppo Hera, come **Inrete Distribuzione**
> — già valutata e **non fattibile** per il cliente residenziale (vedi
> [inrete-protocol.md](inrete-protocol.md)): Inrete rimanda i clienti al
> Portale Consumi ARERA, nessun self-service. AcegasApsAmga è una
> società di distribuzione diversa da Inrete (rete propria a
> Trieste/Padova/Gorizia, non confluita in Inrete), ma il portale
> catturato qui (`servizionline.acegasapsamga.it`, app "MyHera") è
> l'area clienti **unificata** del gruppo — condivide lo stesso bundle
> con altri brand Hera (risulta anche un file di traduzioni servito da
> `servizionline.gruppohera.it`). Non è escluso che le funzioni
> "energia/curve" viste nel bundle siano codice condiviso ma **non
> attivo** per questo brand, come già capitato altrove nel gruppo. Va
> verificato con un account reale prima di dare per scontato che
> funzioni.

Se hai una fornitura AcegasApsAmga attiva (elettrica o gas, con POD/PDR
associato nel portale), puoi aiutare — vedi
[Come contribuire](#come-contribuire) in fondo.

__Le informazioni qui sotto vengono dall'analisi del traffico del
portale (cattura HAR, 15/09/2026) e dal bundle JavaScript dell'app, non
da documentazione ufficiale (che non esiste pubblicamente).__

## Quadro generale

Il portale è un'app **AngularJS** ("MyHera") con autenticazione **Azure
AD B2C** (custom policy, MS Identity Experience Framework) e API REST
proprie sotto `/api`. Il backend anagrafica è **Salesforce**
(`sfdcMigrated: true`, id utente in formato `003Tj...`).

```
PORTALE        = https://servizionline.acegasapsamga.it
LOGIN_HOST     = https://login.acegasapsamga.it
TENANT         = myheraapp.onmicrosoft.com
TENANT_ID      = dc240af9-5df9-404d-8ffa-8afa34792fc7
POLICY         = B2C_1A_SignIn_Web
CLIENT_ID      = 40c94bb1-2d83-4ccc-8c72-fde8ad15ed24
REDIRECT_URI   = https://servizionline.acegasapsamga.it/auth/acegas/login
SCOPE          = openid offline_access
                 https://myheraapp.onmicrosoft.com/40c94bb1-2d83-4ccc-8c72-fde8ad15ed24/read
                 profile
```

## Login — verificato funzionante (15/09/2026)

Flusso OAuth2 **authorization code + PKCE (S256)** standard su un
custom policy Azure AD B2C — niente OTP, ma serve un client HTTP che
segua redirect e gestisca cookie (niente JS/browser richiesto, il
flusso è tutto form POST + redirect 302).

1. `GET {LOGIN_HOST}/{TENANT}/{POLICY}/oauth2/v2.0/authorize` con
   `client_id`, `scope`, `redirect_uri`, `response_type=code`,
   `response_mode=fragment`, `code_challenge`/`code_challenge_method=S256`,
   `nonce`, `state`, `prompt=select_account`. Risposta `200 text/html`:
   la pagina contiene un blob `var SETTINGS = {...}` con `transId` (il
   parametro `tx` dei passi successivi) e `csrf` (uguale al cookie
   `x-ms-cpim-csrf` che il server ha appena impostato).
2. `POST {LOGIN_HOST}/{TENANT}/{POLICY}/SelfAsserted?tx=<transId>&p={POLICY}`
   — header `X-CSRF-TOKEN: <csrf>`, body
   `application/x-www-form-urlencoded`:
   `request_type=RESPONSE&signInName=<email>&password=<password>`.
   Risposta `200 text/json` (successo) o messaggio d'errore.
3. `GET {LOGIN_HOST}/{TENANT}/{POLICY}/api/CombinedSigninAndSignup/confirmed?rememberMe=false&csrf_token=<csrf>&tx=<transId>&p={POLICY}`
   — risposta `302`, header `Location` verso `REDIRECT_URI` con il
   `code` (e `state`, `client_info`) nel **fragment** dell'URL
   (`#code=...`), non nella query string.
4. `POST {LOGIN_HOST}/{TENANT}/{POLICY}/oauth2/v2.0/token` —
   `grant_type=authorization_code`, `client_id`, `redirect_uri`,
   `scope`, `code`, `code_verifier`. Risposta: `access_token`,
   `id_token`, `refresh_token`, `expires_in` standard OIDC.

## Sessione sul portale — verificato funzionante

L'app non usa l'`access_token` B2C direttamente come Bearer sulle API
del portale. Dopo il login:

5. `POST {PORTALE}/api/v1/user/cookie` — body `{"token": "<access_token>"}`.
   Risposta: descrittore di un cookie httpOnly, `__Secure-b2c-access-token`
   (dominio `.acegasapsamga.it`), che il server imposta via `Set-Cookie`
   sulla stessa risposta. Da quel momento le chiamate API vanno fatte
   **con quel cookie** (`withCredentials`), non con `Authorization:
   Bearer`.

Header applicativi visti su ogni chiamata API: `X-Bwb-PlatformId: web`,
`X-Bwb-Referer: <referer>`. Il refresh silenzioso (`TOKEN_EXPIRED` →
refresh_token grant su `{LOGIN_HOST}/{TENANT}/{POLICY}/oauth2/v2.0/token`
→ `/api/v1/user/tk` → nuovo `/api/v1/user/cookie`) è implementato nel
bundle (`ApiClass`) ma non ancora osservato in cattura.

## API anagrafica — verificate funzionanti

Tutte con il cookie di sessione (passo 5).

| Endpoint | Cosa restituisce (account di cattura, "prospect") |
|---|---|
| `GET /api/v1/user` (= `/api/user`) | anagrafica utente: nome, cognome, codice fiscale, email, `prospect: true`, `sfdcMigrated: true` |
| `GET /api/profile/list` | `{"list":[],"hasNext":false}` — profili/utenze associate; vuoto perché nessun contratto |
| `GET /api/profile/prospect/contract/list` | `{"list":[],"hasNext":false,"brand":null}` — contratti (anche in attivazione); vuoto |
| `GET /api/prelogin/welcome` | banner/layout della home, non autenticato |

## Endpoint energia — individuati, non ancora provati con dati reali

Estratti da `assets/scripts/app.min.js` (bundle Angular, minificato),
factory `EnergyManagerService` e `MeterService`. Sono le chiamate che il
frontend sa fare; **mai eseguite in cattura** (l'account non ha
contratti, quindi la sezione "Gestione energia" non è mai stata
raggiunta).

| Metodo service | HTTP | Path | Note |
|---|---|---|---|
| `EnergyManagerService.list()` | `GET` | `/api/energy/pod/list` | elenco POD/PDR dell'utente, paginato; chiamato senza filtri |
| `EnergyManagerService.curves(query)` | `POST` | `/api/energy/measure/list` | body `{fromDate, toDate, pods:[...], aggregationLevel, curveGroup, curveTypes:[...]}` |
| `EnergyManagerService.podCsv()` | `GET` | `/api/energy/pod/export/csv` | export CSV dell'elenco POD |
| `EnergyManagerService.csv()` | `GET` | `/api/energy/measure/export/csv` | export CSV delle misure |
| `MeterService.list(profileId, contractId)` | `GET` | `/api/profile/{profileId}/contract/{contractId}/meter/list` | contatori di un contratto |
| `MeterService.get(profileId, contractId, meterId)` | `GET` | `/api/profile/{profileId}/contract/{contractId}/meter/{meterId}` | dettaglio contatore |

Note dal codice del bundle (route Angular `pod-curves`, controller
`PodCurvesController`):

- `aggregationLevel` visto in uso: `"ZPA"`.
- `curveGroup` — valori individuati: `curvesPotenzaAttiva15` e
  `curvesPotenzaReattiva15` (potenza attiva/reattiva a intervallo di 15
  minuti — terminologia standard di **curva di carico da smart meter**,
  non dato di fatturazione). Ce ne sono probabilmente altri (energia
  attiva per fascia, letture mensili) non enumerati esplicitamente nel
  codice visto finora.
- `i.is15MinMeasure = curveGroup in {curvesPotenzaAttiva15,
  curvesPotenzaReattiva15}` — la UI tratta questi due gruppi come
  "misura a 15 minuti", con un range di date massimo diverso
  (`graphRange15` vs `graphRange`) dagli altri gruppi.
- `fromDate`/`toDate` sono timestamp Unix in millisecondi.
- Esiste anche l'azione self-service di **autolettura**
  (`Autolettura`, `AcquaAutoletturaCommon/Large/Medium/Small`,
  `EnergyAutolettura...`, `GasAutolettura...`, `TLRAutolettura...` —
  acqua/energia/gas/teleriscaldamento), non serve per l'integrazione.

## Cosa manca

**Un account con un contratto AcegasApsAmga attivo e un POD (o PDR gas)
associato.** Con l'account di cattura attuale:

- `/api/profile/list` e `/api/profile/prospect/contract/list` sono
  entrambi vuoti (`"prospect": true` sull'utente) — mai stata raggiunta
  la sezione "Gestione energia" del portale, quindi:
  - non sappiamo se `EnergyManagerService.list()`/`curves()`
    rispondano con dati reali, con un 403 per permessi, o con dati
    lato-fatturazione anziché lato-distributore (il dubbio aperto per
    Inrete, vedi avviso in cima);
  - non conosciamo la forma esatta della risposta di `curves()` (unità
    di misura, granularità effettiva, nomi dei campi);
  - non conosciamo gli altri valori possibili di `curveGroup`/`curveTypes`.

## Come contribuire

Serve qualcuno con una **fornitura AcegasApsAmga attiva** (elettrica o
gas) e il POD/PDR già associato nel portale servizionline.acegasapsamga.it.

Lo script [`scripts/verify_acegasapsamga_login.py`](../scripts/verify_acegasapsamga_login.py)
automatizza i passi di login e anagrafica descritti sopra e, se trova
profili/contratti associati, prova a interrogare `EnergyManagerService`.
Chiede utenza e password (la password non viene salvata né stampata) e
non invia nulla a terzi: fa solo richieste dirette al portale
AcegasApsAmga con lo stesso traffico che farebbe il tuo browser.

```bash
pip install requests
python3 scripts/verify_acegasapsamga_login.py
```

In alternativa, va benissimo anche una **cattura HAR** della pagina
"Gestione energia" / "Analisi consumi" con un grafico caricato: dalla
cattura attuale (account senza contratti) non è mai stato possibile
raggiungerla. Attenzione: **non è anonimizzata** (contiene token di
sessione e dati personali in chiaro), quindi non allegarla a una issue
pubblica.
