# SET Distribuzione — protocollo

**SET Distribuzione S.p.A.** (Rovereto, gruppo Dolomiti Energia) — portale
`myset.setdistribuzione.it`. **Supportato** dal pacchetto
`distributors/setdistribuzione/`.

Due catture reali:

- **13/09/2026**, account **senza fornitura** (profilo `Prospect`), export
  di Safari: login e anagrafica, nessun dato di consumo.
- **13/10/2026**, account **con un POD attivo** (profilo `Retail_SET`),
  export di Firefox con gli header: login, forniture e consumi fino al
  quarto d'ora. Ha sciolto il dubbio della prima cattura: gli endpoint di
  consumo restituiscono **letture del contatore 2G**, non dati di
  fatturazione (vedi sotto).

__Le informazioni qui sotto vengono dall'analisi del traffico del
portale (catture HAR) e dal bundle JavaScript dell'app, non da
documentazione ufficiale (che non esiste pubblicamente).__

## Quadro generale

Il login passa da **Azure AD B2C** (Microsoft), non dal protocollo PCF
(Duereti/Unareti) né da quello Salesforce/OTP di E-Distribuzione:
protocollo strutturalmente nuovo, **caso B** di
[`CONTRIBUTING.md`](../../CONTRIBUTING.md).

La SPA (`myset.setdistribuzione.it`) è una web app React che espone la
sua config al caricamento come `window.privatearea`:

```json
{
  "theme": "set",
  "baseApiUrl": "https://agw.setdistribuzione.it/api",
  "siteName": "mySET",
  "companyName": "Set Distribuzione",
  "authDomain": "sportelloclientib2cset.b2clogin.com",
  "authPath": "sportelloclientib2cset.onmicrosoft.com",
  "clientId": "3cfe517e-7c9e-433e-a9dd-daf4cca499c9",
  "redirecturi": "https://myset.setdistribuzione.it"
}
```

```
BASE_API     = https://agw.setdistribuzione.it/api
AUTH_DOMAIN  = sportelloclientib2cset.b2clogin.com
TENANT       = sportelloclientib2cset.onmicrosoft.com
POLICY       = B2C_1A_signin
CLIENT_ID    = 3cfe517e-7c9e-433e-a9dd-daf4cca499c9
REDIRECT_URI = https://myset.setdistribuzione.it
SCOPE        = https://sportelloclientib2cset.onmicrosoft.com/private/api/read openid profile offline_access
PIVA         = 01932800228 (confermata da ARERA per il comune di Rovereto, 13/10/2026)
```

> [!NOTE]
> Il bundle JS di questa SPA è **condiviso con myDOLOMITI** (il portale
> del venditore del gruppo Dolomiti Energia): stesso codice React/Redux
> Toolkit Query, stesso `authDomain` di default
> (`dolomitienergiab2c.b2clogin.com`) sovrascritto a runtime da
> `window.privatearea` col tenant specifico di SET. Utile da sapere se
> in futuro si guarda a un altro distributore/venditore dello stesso
> gruppo: probabilmente riusa lo stesso schema.

## Login — verificato con cattura reale (13/09/2026)

Flusso standard Azure AD B2C "sign in" via MSAL.js (Authorization Code
+ PKCE), niente password grant diretto come Ireti — bisogna passare
dalla pagina ospitata su `b2clogin.com`. Confermato identico dalla
cattura del 13/10/2026. Implementato in `auth.py`:

1. `GET {AUTH_DOMAIN}/{TENANT}/{POLICY}/oauth2/v2.0/authorize?client_id=...&code_challenge=...&code_challenge_method=S256&response_type=code&response_mode=fragment&...`
   → pagina HTML con il blob `var SETTINGS = {...}` (`csrf`, `transId`) e i
   cookie `x-ms-cpim-*` della transazione
2. `POST {AUTH_DOMAIN}/{TENANT}/{POLICY}/SelfAsserted?tx={transId}&p={POLICY}`
   con `request_type=RESPONSE&signInName=<email>&password=<password>` e
   header `X-CSRF-TOKEN: {csrf}` → `{"status":"200"}`
3. `GET {AUTH_DOMAIN}/{TENANT}/{POLICY}/api/CombinedSigninAndSignup/confirmed?rememberMe=false&csrf_token={csrf}&tx={transId}&p={POLICY}`
   → 302 verso `REDIRECT_URI#state=...&client_info=...&code=...`
4. `POST {AUTH_DOMAIN}/{TENANT}/{POLICY}/oauth2/v2.0/token` — scambio
   `code` (+ `code_verifier`) → token

Credenziali errate al passo 2 (verificato dal vivo il 13/10/2026 con un
account inesistente): HTTP 200 con
`{"status":"400","errorCode":"AADB2C90053","message":"Credenziali non valide."}`.

Risposta del passo 4, shape OIDC standard:

```json
{
  "access_token": "...",
  "id_token": "...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "refresh_token": "...",
  "refresh_token_expires_in": 86400,
  "scope": "https://sportelloclientib2cset.onmicrosoft.com/private/api/read",
  "client_info": "..."
}
```

`access_token` da **1 ora**, `refresh_token` da **24 ore**. Per i client
"Single-Page Application" B2C non rinnova il refresh token oltre le 24
ore dal login: l'integrazione non conserva token e rifà il login
completo nei cicli in cui deve scaricare qualcosa (come Ireti).

L'`id_token` e l'`access_token` contengono in chiaro **codice fiscale,
email, nome e cognome** (claim `codice_fiscale`, `email_address`,
`given_name`, `family_name`): non vanno mai loggati, e una cattura HAR
li contiene in più punti (risposta del token endpoint, header
`Authorization` di ogni chiamata).

> [!WARNING]
> **Gotcha cookie (aiohttp):** i valori dei cookie `x-ms-cpim-*`
> contengono `/`, `+` e `=`. Il cookie jar di default di aiohttp li
> rimanda tra virgolette, B2C non li riconosce e risponde
> **400 "Bad Request"** (testo semplice, non JSON) al passo 2. La
> sessione va creata con `aiohttp.CookieJar(quote_cookie=False)`
> (`auth.nuovo_cookie_jar()`). Verificato dal vivo il 13/10/2026: con
> lo stesso codice e il jar non quotato arriva la normale risposta JSON.

> [!NOTE]
> Il token endpoint riceve l'header `Origin: https://myset.setdistribuzione.it`,
> come nel browser: per i client registrati come SPA, B2C rifiuta lo
> scambio del code senza una richiesta cross-origin (errore
> `AADSTS9002327`). Comportamento documentato da Microsoft, non provato
> su questo tenant senza Origin.

## API — verificate su account con POD (13/10/2026)

Tutte su `{BASE_API}` con `Authorization: Bearer <access_token>` (header
confermato dalla cattura Firefox). Nessun altro header o cookie
richiesto.

| Endpoint | Metodo | Cosa restituisce |
|---|---|---|
| `/b2c/alert` | GET | banner applicativo (anche prima del login): `show`, `message`, `iconName` |
| `/secure/profile/registration` | GET | anagrafica: `email`, `cellphone`, `givenName`, `surname`, `fiscalCode`, `customerType` (`"RETAIL"`), `profiles` (`["Retail_SET"]`, o `["Prospect"]` senza forniture) |
| `/secure/profile/customer` | GET | `data`: un elemento per profilo, con `fiscalCode`, `profile`, `businessPartner`, `defaultProfile`. Lista vuota per un account Prospect |
| `/secure/utility/{fiscalCode}/active?profile=` | GET | `data.utilities`: `podPdr`, `contractId`, `utilityType` (`"ENERGY"`), `utilityStatus` (`"ACTIVE"`), `name`, `utilityAddress`, `businessPartner`, `pesseGroup` |
| `/secure/utility/detail/{fiscalCode}/{contractId}?profile=` | GET | dettaglio fornitura: `meterSerialNumber`, `meterType` (`"2G"`), `contractualPower`, `availablePower`, `voltage`, `phase`, `tariffCategory` |
| `/secure/utility/{fiscalCode}/{contractId}/consumption` | GET | consumi, vedi sotto |
| `/secure/utility/reports/{fiscalCode}?profile=` | GET | prossima lettura programmata (vuota nella cattura) |
| `POST /secure/utility/pesse/emergency` | POST | piano di distacco PESSE dei POD (`{"fc","profile","podBp"}`) |

`{fiscalCode}` nel path è il codice fiscale del titolare (nella cattura
mascherato a mano da chi l'ha fatta, come i campi dell'anagrafica).
L'integrazione lo legge da `/secure/profile/customer` insieme al
profilo, poi le forniture da `utility/active`; scarta quelle con
`utilityType` diverso da `ENERGY` (il portale gestisce anche gas e
teleriscaldamento del gruppo).

### Consumi

`GET /secure/utility/{fiscalCode}/{contractId}/consumption` con
`consumptionRange=F1_F2_F3`, `consumptionType=A1`, `profile`,
`supplyPoint={POD}`, `year`, e opzionalmente `month` e `day`. Tre
livelli di dettaglio:

| Parametri | `data.consumptions[]` | Note |
|---|---|---|
| solo `year` | un elemento per mese: `month`, `total` (kWh), `estimated` | `data.total` = somma dell'anno |
| `year` + `month` | un elemento per giorno: `day`, `total`, `average` | in più `data.powerPeak`, es. `"sabato 3 ore 19: 2,472 kW"` |
| `year` + `month` + `day` | un elemento per ora: `hour` (0-23), `quarters` (4 valori), `total`, `average` | **usato dall'integrazione** |

> [!IMPORTANT]
> **`month` nella richiesta parte da 0**: `month=9` restituisce ottobre.
> Nelle risposte invece `month` è 1-based (`"month": 10`). `day` è
> 1-based in entrambi.

Elemento orario reale (12/10/2026):

```json
{
  "drillDownAvailable": true,
  "year": 2026, "month": 10, "day": 12, "hour": 13,
  "quarters": [0.268, 0.457, 0.085, 0.072],
  "total": 0.88199997,
  "average": 0.22049999,
  "estimated": false,
  "meterSerialNumber": "...",
  "kConstant": "1"
}
```

**Unità: kWh per intervallo.** Conferma interna alla cattura: il
`powerPeak` del giorno (`"lunedì 12 ore 13: 1,828 kW"`) è esattamente il
quarto d'ora più alto (0,457 kWh) moltiplicato per 4. `total` dell'ora è
la somma dei `quarters`, e la somma delle 24 ore coincide con `data.total`
del giorno. Sono **misure del contatore 2G del distributore**, non dati di
fatturazione: c'è il dettaglio a 15 minuti e il flag `estimated` per ora.

## Design dell'integrazione

- **Una richiesta per giorno**: solo il livello con `day` ha l'ora, quindi
  ogni giorno da importare costa una chiamata. Le statistiche si scrivono
  a blocchi (un ciclo, o 31 giorni di `recupera_storico`), non una volta
  per giorno: ogni scrittura rilegge e riscrive l'intera serie.
- **Coda per-POD** dei giorni da riprovare, come E-Distribuzione e Ireti:
  ogni ciclo chiede ieri (se non è già nelle statistiche) più i giorni in
  coda; un giorno vuoto o incompleto entra in coda e viene abbandonato
  con un avviso dopo 7 giorni.
- **Orario casuale per installazione tra le 18:00 e le 22:00**, come
  E-Distribuzione (16-20): scelto alla configurazione e salvato nelle
  opzioni (`orario_richiesta`), così le installazioni non interrogano il
  portale tutte insieme. Il primo ciclo dopo la configurazione chiede
  subito ieri per verificare login e POD; poi si chiede solo dopo
  l'orario. Il giorno precedente era completo alle 22:23 nella cattura,
  prima non è verificato: se manca, il ciclo orario lo richiede.
- **Ciclo orario, login solo se serve**: prima dell'orario, o con ieri già
  nelle statistiche, il ciclo non contatta il portale.
- **`recupera_storico`**: limite di cortesia di 366 giorni per azione
  (una chiamata al giorno).

## Cosa resta aperto

1. **Cambio ora**: nessuna cattura contiene un giorno da 23 o 25 ore.
   L'integrazione importa un giorno solo se il numero di ore coincide con
   la durata reale della giornata (23/24/25), mappando l'i-esima ora come
   tempo trascorso dalla mezzanotte (lo schema verificato per Ireti). Se
   il portale restituisce sempre 24 ore anche in quei giorni, il giorno
   viene scartato con un avviso nei log: è il primo punto da controllare
   dopo il 25/10/2026.
2. **Ora di pubblicazione** del giorno precedente: da misurare (lo
   script `scripts/verify_setdistribuzione_login.py` dice se un giorno
   c'è già).
3. **Risposta per un giorno non ancora pubblicato** (lista vuota o 404):
   mai vista, entrambe trattate come "non ancora disponibile".
4. **Valori `estimated`**: importati come gli altri; non sappiamo se il
   portale li corregge in seguito né quando.

## Verifica da terminale

`scripts/verify_setdistribuzione_login.py` importa `auth.py`/`api.py`
dell'integrazione e prova login, forniture e ore di un giorno, stampando
POD e contratto mascherati (vedi [`scripts.md`](../scripts.md)).

Per una nuova cattura: Chrome o Firefox (Safari toglie gli header
`Authorization` e `Cookie` dall'export), **Preserve log** attivo prima del
login, poi "Export HAR with content". **Non allegare la HAR grezza a una
issue pubblica**: contiene token validi per 24 ore, codice fiscale (nei
token), POD, indirizzi e, se il browser ha fatto altre richieste durante
la cattura, anche cookie di sessione di altri siti.
