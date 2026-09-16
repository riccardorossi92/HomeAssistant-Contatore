# Ireti — stato della ricerca (supporto non ancora implementato)

Ireti (`smartpod.ireti.it`) **non è ancora supportato**, ma è **fattibile**.
L'autenticazione e l'anagrafica sono verificate con dati reali; gli
**endpoint dei consumi ora sono noti** (estratti dal bundle dell'app, vedi
[Endpoint di misura](#endpoint-di-misura--individuati-non-ancora-provati-con-dati-reali)).
Manca solo la conferma della struttura delle risposte da un account con
un POD associato.

> A differenza di **Areti** (portale di sole pratiche, nessun dato di
> misura — vedi [areti-protocol.md](areti-protocol.md)), qui il backend
> di misura esiste ed è ricco: letture per fascia **e** curva di carico
> a 15 minuti.

Se hai una fornitura Ireti attiva, puoi aiutare — vedi
[Come contribuire](#come-contribuire) in fondo.

__Le informazioni qui sotto vengono dall'analisi del traffico del portale
(cattura HAR) e da test diretti contro le API in produzione, non da
documentazione ufficiale (che non esiste pubblicamente).__

## Quadro generale

Il portale è un'app **Angular** con backend **Keycloak** per
l'autenticazione (OpenID Connect standard) e API REST per i dati.
Rispetto agli altri distributori supportati sarebbe il più semplice:
niente ticket/export asincrono come PCF, niente OTP né scraping HTML come
E-Distribuzione. Solo REST con Bearer token.

```
BASE      = https://smartpod.ireti.it
REALM     = IRENSmartPOD
CLIENT_ID = SmartPOD-Angular      (client pubblico: nessun client_secret)
TOKEN_URL = {BASE}/auth/realms/{REALM}/protocol/openid-connect/token
```

## Login — verificato funzionante (31/08/2026)

Il realm accetta il **password grant diretto**: una sola richiesta, niente
scraping del form di login, niente PKCE.

```
POST {TOKEN_URL}
  grant_type=password
  client_id=SmartPOD-Angular
  username=<utente>
  password=<password>
  scope=openid profile email
```

Risposta: `access_token`, `refresh_token`, `id_token` standard OIDC.

Esiste anche il flusso `authorization_code` (quello che usa il browser:
`GET .../openid-connect/auth` → form → `POST .../login-actions/authenticate`
→ `code` → `POST .../token`), verificato anch'esso su cattura reale, ma il
password grant lo rende superfluo.

> [!WARNING]
> **Il `refresh_token` dura 30 minuti** (`refresh_expires_in: 1800`),
> mentre l'`access_token` dura 5 ore (`expires_in: 18000`). Con un
> polling orario come quello usato per gli altri distributori, al secondo
> ciclo il refresh token sarebbe già scaduto: servirebbe conservare le
> credenziali e rifare il login periodicamente — cosa che oggi nessun
> altro distributore richiede. Da tenere presente in fase di progettazione.

> [!WARNING]
> **Gli endpoint `/users/*` non rispondono senza header da browser.** Una
> richiesta senza `User-Agent`, `Referer`, `Accept` e `Sec-Fetch-*` va in
> **timeout** — non riceve un 403, la connessione viene semplicemente
> scartata (comportamento tipico di un WAF). Con quegli header presenti,
> le stesse chiamate funzionano.

## API note — verificate funzionanti

Tutte con `Authorization: Bearer <access_token>` e gli header da browser
di cui sopra.

| Endpoint | Cosa restituisce |
|---|---|
| `GET /users/public/company-by-host` | `idCompany` (per IRETI: `7f000001-78d0-1923-8178-d07d36e20003`) |
| `GET /users/consumer/getbykeycloakusername/{username}` | anagrafica in `entityModel[0]`, incluso `idConsumer` |
| `GET /users/pods/getallbyconsumerandcompany/{idConsumer}/{idCompany}` | elenco dei POD dell'utente |
| `GET /users/pods/getallbyconsumer/{idConsumer}` | elenco POD (variante senza company), include `details` (podType, podMaxPower…) |

## Endpoint di misura — individuati, non ancora provati con dati reali

Estratti dal bundle `main-es2015.*.js` dell'app (HAR della pagina
`/prelievi`, 04/09/2026). Sono le chiamate che il frontend Angular sa
fare; payload e semantica dei campi ricavati dal codice del bundle, **non
ancora verificati contro risposte reali** (l'account di cattura ha
`pods: []`).

| Endpoint | Metodo | Payload | Cosa dovrebbe restituire |
|---|---|---|---|
| `/users/exabeat/history` | POST | `{customerTaxCodeVat, pod, startDate, endDate}` | `customerPodActive` (bool), `podType` (`orario` / `fasce` / `mono orario`) |
| `/users/exabeat/registry` | POST | idem | anagrafica tecnica del POD |
| `/readings/exabeat/measures` | POST | `{customerTaxCodeVat, pod, startDate, endDate}` | letture periodiche: `energyActiveF1/F2/F3`, `energyReactiveF1..3`, `energyPowerF1..3` (per fascia) |
| `/readings/exabeat/measures-loadprofiles` | POST | idem | `loadProfiles[]`: ogni voce ha `loadProfileDate` (`DD/MM/YYYY`) e `sampleValues` — **curva di carico a 15 min**, 92 campioni/giorno normalmente, portati a 96 (il frontend inserisce 4 `undefined` a indice 8 per il cambio ora legale) |
| `/readings/exabeat/can-request-verify` | POST | `{...}` | se è possibile richiedere una verifica misuratore |
| `/readings/selfreading/save`, `/readings/selfreading/checkotp` | POST | — | autolettura (con OTP) — non serve per l'integrazione |

Note dal codice del bundle:

- `customerTaxCodeVat` = `pIVA` se presente, altrimenti `codFiscale` del
  consumer (da `getbykeycloakusername`).
- `pod` = il **codice** POD (campo `code`), non `idPod`.
- `startDate` / `endDate` sono oggetti `Date` serializzati (il frontend
  usa `new Date(anno, mese, 0, 23, 59, 59)`); per `history` il default è
  una finestra di 12 mesi, per le misure la finestra scelta nel datepicker.
- `measures-loadprofiles` è servito da un metodo chiamato
  `getPodMeasuresProxy` → i dati passano da un proxy lato server Ireti.
- La curva a 15 min è **più granulare** del dato giornaliero di
  E-Distribuzione: è il valore aggiunto principale di questa integrazione.

## Cosa manca

**Solo le risposte reali degli endpoint qui sopra.** Le catture
disponibili finora vengono da un account **senza POD attivi**
(`getbykeycloakusername` → `"pods": []`, `getallbyconsumerandcompany` →
`entityModel: null`): la pagina dei prelievi non ha mai chiamato le API di
misura, quindi non sappiamo la forma esatta delle risposte né le unità di
`sampleValues` (kW medi sul quarto d'ora? kWh?).

### Associazione del POD

È un'azione self-service, non un limite tecnico:
`POST /users/pods/save` con `{code, name, consumerId, companyId}` —
niente OTP né numero bolletta nel payload; la validazione (match
intestatario ecc.) è lato backend, che risponde `reponseCode: 300` se
l'associazione riesce. Si fa dal portale: **"Aggiungi POD" → codice POD**.
L'informativa privacy del portale indica il POD come dato richiesto in
fase di registrazione, quindi un account Ireti "normale" dovrebbe avere
già il POD associato — quello di cattura è un caso anomalo.

## Come contribuire

Serve qualcuno con una **fornitura Ireti attiva e almeno un POD
associato** nel portale.

C'è uno script che fa tutto da solo:

```bash
pip install requests
python3 scripts/raccogli_dati_ireti.py
```

Ti chiede utenza e password (la password non compare a schermo e non
viene salvata), poi:

1. fa il login;
2. legge la tua anagrafica e i tuoi POD;
3. scarica il bundle JavaScript dell'app — è un file pubblico, lo scarica
   anche il tuo browser ogni volta che apri il sito — e ne estrae
   l'elenco degli endpoint che l'app sa chiamare: è così che scopriamo
   gli endpoint dei consumi senza doverli indovinare;
4. prova quelli che sembrano di misura sul tuo POD e ne registra la
   struttura delle risposte.

Alla fine trovi un file `ireti_report.json` da allegare a una issue.

**Sulla privacy**: lo script non invia niente a nessuno, scrive solo un
file locale che decidi tu se e a chi mandare. Il report è anonimizzato in
automatico — nome, cognome, codice fiscale, email, telefono, token e
identificativi personali diventano segnaposto, il codice POD è mascherato
mantenendo solo il formato, e dei dati di consumo restano pochi campioni
(serve la struttura delle risposte, non i tuoi consumi). Detto questo,
**aprilo e dagli un'occhiata prima di allegarlo**: se c'è qualcosa che non
vuoi condividere, cancellalo pure — serve la forma, non il contenuto.

In alternativa allo script, va benissimo anche una **cattura HAR** della
pagina dei prelievi con un grafico caricato: contiene le stesse
informazioni, ma **non è anonimizzata** (contiene token di sessione e
dati personali in chiaro), quindi in quel caso non allegarla a una issue
pubblica.
