# SET Distribuzione — stato della ricerca (supporto non ancora implementato)

**SET Distribuzione S.p.A.** (Rovereto, gruppo Dolomiti Energia) — portale
`myset.setdistribuzione.it`. **Non ancora supportato.**

Login e anagrafica sono verificati con una cattura reale. Non è ancora
noto se il portale espone dati di misura del distributore (curva di
carico / letture), perché l'unica cattura disponibile viene da un
account **senza fornitura associata** (profilo `Prospect`).

__Le informazioni qui sotto vengono dall'analisi del traffico del
portale (cattura HAR) e dal bundle JavaScript dell'app, non da
documentazione ufficiale (che non esiste pubblicamente).__

## Quadro generale

Il login passa da **Azure AD B2C** (Microsoft), non dal protocollo PCF
(Duereti/Unareti) né da quello Salesforce/OTP di E-Distribuzione:
protocollo strutturalmente nuovo, rientra nel **caso B** di
[`CONTRIBUTING.md`](../CONTRIBUTING.md).

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
POLICY       = b2c_1a_signin
CLIENT_ID    = 3cfe517e-7c9e-433e-a9dd-daf4cca499c9
REDIRECT_URI = https://myset.setdistribuzione.it
SCOPE        = https://sportelloclientib2cset.onmicrosoft.com/private/api/read openid profile offline_access
PIVA (da footer del sito pubblico) = 01932800228
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
dalla pagina ospitata su `b2clogin.com`:

1. `GET {AUTH_DOMAIN}/{TENANT}/{POLICY}/v2.0/.well-known/openid-configuration`
2. `GET {AUTH_DOMAIN}/{TENANT}/{POLICY}/oauth2/v2.0/authorize?client_id=...&code_challenge=...&code_challenge_method=S256&response_type=code&response_mode=fragment&...`
3. `POST {AUTH_DOMAIN}/{TENANT}/{POLICY}/SelfAsserted?tx=...&p={POLICY}` — invio email+password (pagina ospitata da B2C, non da myset)
4. `GET {AUTH_DOMAIN}/{TENANT}/{POLICY}/api/CombinedSigninAndSignup/confirmed?...` → redirect 302 con `code` in fragment verso `REDIRECT_URI`
5. `POST {AUTH_DOMAIN}/{TENANT}/{POLICY}/oauth2/v2.0/token` — scambio `code` (+ `code_verifier`) → token

Risposta del passo 5, shape OIDC standard:

```json
{
  "access_token": "...",
  "id_token": "...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "refresh_token": "...",
  "refresh_token_expires_in": 86400,
  "scope": "...",
  "client_info": "..."
}
```

`refresh_token` dura **24h** (`refresh_token_expires_in: 86400`),
`access_token` **1h** (`expires_in: 3600`) — nessun problema per un
polling orario, a differenza del caso Ireti (refresh token da 30 min).

> [!WARNING]
> La cattura è stata fatta con l'export HAR di **Safari/WebKit**
> (`creator.name: "WebInspector"`): **non contiene nessun header
> `Authorization` né `Cookie`, su nessuna delle 298 richieste**,
> incluse quelle autenticate verso `agw.setdistribuzione.it`. Non è
> un'assenza di autenticazione (il `token_type` è esplicitamente
> `"Bearer"`) — è quasi certamente Safari che **redige questi header
> per privacy** nell'export. Per le prossime catture su questo o altri
> distributori, usare **Chrome/Firefox DevTools** (Network → Preserve
> log → Export HAR with content), che non hanno questo comportamento:
> senza l'header reale non sappiamo se è `Authorization: Bearer <token>`
> semplice o se serve qualcos'altro (header custom, cookie di sessione
> in aggiunta).

## API — verificate funzionanti (account senza fornitura, profilo "Prospect")

Tutte su `{BASE_API}`, presumibilmente con `Authorization: Bearer
<access_token>` (vedi warning sopra).

| Endpoint | Metodo | Cosa restituisce |
|---|---|---|
| `/b2c/alert` | GET | banner applicativo: `show` (bool), `message`, `iconName` |
| `/secure/profile/registration` | GET | anagrafica: `email`, `cellphone`, `givenName`, `surname`, `fiscalCode`, `customerType` (`"RETAIL"`), `profiles` (`["Prospect"]`), `assistant`/`superAdmin`/`isReadOnly` (bool), `displayName` |
| `/secure/profile/customer` | GET | `{"message": "Profile retrieved", "data": []}` — **nessun contratto/POD** |
| `/secure/termsOfService` | GET | `{changed, termsOfService: {title, text, lastTimeChanged}}` |
| `/secure/profile/image/{fiscalCode}?profile=Prospect` | GET | **403** — nessuna immagine per un profilo Prospect |

`profiles: ["Prospect"]` è la conferma diretta, lato API, del motivo per
cui l'account di cattura non ha POD: non è ancora un cliente attivo con
una fornitura, è solo un utente registrato al portale.

## Endpoint di consumo/misura — individuati nel bundle, MAI chiamati, non verificati

Estratti dalle definizioni RTK Query nel bundle
`clientlibs-reactbuild-area-privata/resources/static/js/*.chunk.js`
(stesso tipo di analisi statica fatta per Ireti). **Nessuno di questi è
mai stato chiamato nella cattura** (l'account Prospect non ha nulla da
mostrare), quindi non abbiamo risposte reali né conferma che siano
raggiungibili per un profilo diverso da Prospect.

| Nome (RTK Query) | Metodo | Path (relativo a `{BASE_API}`) | Query params |
|---|---|---|---|
| `getUtilityConsumptionUsingGet` | GET | `/secure/utility/{fiscalCode}/{contractualAccount}/{contractId}/consumption` | `consumptionRange`, `consumptionType`, `day`, `month`, `profile`, `supplyPoint`, `year` |
| `getUtilityConsumptionFileUsingGet` | GET | `.../consumption/excel` | come sopra + `hour` |
| `getSuppliesConsumptionFileUsingGet` | GET | `/secure/utility/{fiscalCode}/consumption/excel/{year}/{month}` | `profile` |
| `getUtilityDetailsUsingGet` | GET | `/secure/utility/detail/{fiscalCode}/{contractId}` | `contractualAccount`, `profile` |
| `getMultisiteUtilityDetailsUsingGet` | GET | `/secure/utility/{fiscalCode}/{contractualAccount}` | `profile` |
| `getUtilityMeterSelfReadingUsingGet` | GET | `.../{contractId}/self-reading/{utilityType}/{utilityPoint}` | `profile` |
| `postUtilityMeterSelfReadingUsingPost` | POST | idem | — (autolettura) |
| `getUtilityMeterReadFileUsingGet` | GET | `.../{contractId}/{meterSerialNumber}/meter-read/excel` | `profile`, `year` |
| `getUtilityMeterReadUsingGet` | GET | `/secure/utility/{fiscalCode}/{pdc}/{meterSerialNumber}/meter-read...` | — |
| `getBillsUsingGet` | GET | `/secure/bill/{fiscalCode}` | `billStatus`, `contractualAccount`, `month`, `page`, `podPdr`, `profile`, `relatedBusinessPartner`, `size`, `supplyType`, `year` |
| `getUtilityDocumentsUsingGet` | GET | `/secure/utility/documents/{fiscalCode}` | `commodity`, `month`, `page`, `podPdr`, `profile`, `relatedBusinessPartner` |

Note dal bundle:

- `podPdr` è un parametro unico (POD elettrico o PDR gas): il portale
  gestisce più commodity insieme — un `enum` nel bundle elenca
  `Energy`/`Gas`/`Teleriscaldamento`.
- `consumptionType`, `consumptionRange` e il parametro `hour` (solo
  sulla variante `/excel`) suggeriscono una granularità selezionabile,
  ma senza una risposta reale non si sa il formato né se il livello di
  dettaglio arriva a curva di carico o si ferma al dato mensile.

> [!WARNING]
> **Il bundle è condiviso con myDOLOMITI** (venditore del gruppo, non
> distributore): lo schema `contractId`/`contractualAccount`/
> `actualAccount`/`getBillsUsingGet` è tipico di un rapporto di
> **fornitura/fatturazione con un venditore**, non delle letture di rete
> di un distributore puro. Non è da escludere che questi endpoint
> restituiscano dati di fatturazione (kWh a consuntivo mensile) invece
> di una vera curva di carico da smart meter — o che per un account SET
> Distribuzione "reale" (senza rapporto di vendita nel gruppo) tornino
> semplicemente vuoti/non pertinenti. Questo cambia molto l'utilità di
> questi dati per l'integrazione: va verificato con un account vero
> prima di scriverci sopra del codice.

## Cosa manca

1. Un account **non-Prospect**, cioè con almeno un POD associato, per
   vedere le risposte reali di uno qualsiasi degli endpoint di
   consumo/misura sopra.
2. Con quell'account: capire se `consumption` è dato di fatturazione o
   dato di misura del distributore (vedi warning sopra) — decide se
   questi endpoint sono utili o un vicolo cieco come Areti.
3. Una cattura fatta con **Chrome o Firefox** (non Safari), per vedere
   l'header `Authorization` reale sulle chiamate a `agw.setdistribuzione.it`.

## Come contribuire

Serve chi ha un'utenza mySET con una fornitura SET Distribuzione attiva
(POD associato, profilo diverso da `Prospect`). Non c'è ancora uno
script dedicato come `scripts/raccogli_dati_ireti.py` — per ora vale la
cattura HAR manuale: login + pagina consumi con un grafico caricato,
con **Chrome o Firefox** (Preserve log attivo *prima* di navigare,
"Export HAR with content" per includere i body delle risposte).

Vedi [Aiutare senza scrivere codice](../CONTRIBUTING.md#aiutare-senza-scrivere-codice-raccolta-dati)
in CONTRIBUTING.md: **non allegare la HAR grezza a una issue
pubblica** (contiene token di sessione e dati personali in chiaro) —
apri prima una issue per concordare come condividerla.
