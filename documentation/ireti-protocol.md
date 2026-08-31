# Ireti — stato della ricerca (supporto non ancora implementato)

Ireti (`smartpod.ireti.it`) **non è ancora supportato**. L'autenticazione
e l'anagrafica sono già state analizzate e verificate con dati reali;
manca il pezzo decisivo: **gli endpoint dei consumi**.

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

## Cosa manca

**Gli endpoint dei consumi.** Le catture disponibili finora vengono da un
account **senza POD attivi**: la pagina dei prelievi non ha quindi mai
chiamato nessuna API di misura, e senza quelle non c'è nulla da importare
nella Energy Dashboard — cioè manca proprio il senso dell'integrazione.

Non sono indovinabili: provare URL a caso su un'API non documentata è il
modo peggiore di procedere (con E-Distribuzione ha funzionato proprio
l'approccio opposto — sempre partire da catture reali).

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
