# E-Distribuzione — protocollo (reverse engineering)

__Tutte le informazioni sotto sono state ottenute analizzando il traffico
dell'app E-Distribuzione reale (cattura HAR) e con test diretti contro le
API in produzione, non da documentazione ufficiale (che non esiste
pubblicamente per questo flusso).__<br>
__Usa queste informazioni responsabilmente.__

Il codice che implementa questo protocollo è in
`custom_components/contatore_letture/distributors/edistribuzione/`
(`auth.py` per il login, `api.py` per le chiamate dati).

## Foreword

Il backend è **Salesforce Experience Cloud** (login/OTP: OAuth2+PKCE via
Aura framework + scraping di pagine Visualforce) più un layer REST separato
su **MuleSoft/CloudHub** per i dati di misura (POD, letture, curva di
carico). Sono due sistemi diversi con convenzioni diverse (header, formati
di errore), collegati solo dal token OAuth ottenuto dal primo e usato come
Bearer sul secondo.

## Base URL e endpoint

```
SF_BASE = https://private.e-distribuzione.it/PortaleClienti

OAUTH_AUTHORIZE_URL = {SF_BASE}/services/oauth2/authorize
OAUTH_TOKEN_URL     = {SF_BASE}/services/oauth2/token
OAUTH_USERINFO_URL  = {SF_BASE}/services/oauth2/userinfo
AURA_ENDPOINT       = {SF_BASE}/s/sfsites/aura
LOGINFLOW_URL       = {SF_BASE}/loginflow/loginFlow.apexp

REDIRECT_URI = eneldist://redirect   (custom scheme dell'app mobile, mai
                                       seguito realmente: intercettiamo il
                                       code prima che ci si arrivi)

Backend dati (MuleSoft): xs-misura-p.de-c1.eu1.cloudhub.io/xs/misure/*
```

## Flusso di login (email + password + OTP)

### 1. `GET {OAUTH_AUTHORIZE_URL}` (con PKCE)

Genera `code_verifier`/`code_challenge` (S256) e uno `state` casuale,
richiede la pagina di login.

> [!WARNING]
> **Loop di redirect infinito.** La catena di redirect da questo endpoint
> passa per un parametro `startURL` che contiene un URL annidato già
> percent-encoded. Lasciare che aiohttp segua i redirect da solo
> (`allow_redirects=True`) può entrare in un loop infinito
> (`TooManyRedirects`), a seconda di come la libreria ri-codifica l'URL già
> codificato ad ogni hop. **Fix**: seguire i redirect a mano con
> `encoded=True` sull'URL del prossimo hop (vedi
> `_get_following_redirects` in `auth.py`).

La pagina di login finale (Salesforce Experience Cloud / Aura) contiene un
blob JSON incorporato **percent-encoded nel path** di uno script di
bootstrap, non in una variabile JS in chiaro:

```
<script src="/PortaleClienti/s/sfsites/l/%7B%22mode%22%3A%22PROD%22
  %2C%22fwuid%22%3A%22<fwuid>%22%2C%22loaded%22%3A%7B%22APPLICATION%40
  markup%3A%2F%2Fsiteforce%3AloginApp2%22%3A%22<versione>%22%7D%7D/app.js">
```

Da qui si estraggono due valori necessari al passo successivo:
- `fwuid`: identificativo di build del framework Aura;
- `loaded`: mappa `{"APPLICATION@markup://siteforce:loginApp2": "<versione>"}`.

> [!WARNING]
> **`loaded` non può essere `{}` vuoto.** Se `aura.context` viene inviato
> con `loaded` vuoto invece del valore reale sopra, il server risponde con
> un generico `AuraClientInputException` ("Unexpected request input"),
> senza indicare la vera causa.

### 2. `POST {AURA_ENDPOINT}?r=2&other.PED_Login.loginUser=1`

Corpo (`application/x-www-form-urlencoded`):

```
message      = {"actions":[{"id":"1;a","descriptor":
                "apex://PED_LoginController/ACTION$loginUser",
                "callingDescriptor":"markup://c:PED_Login","params":
                {"username":"<email>","password":"<password>",
                "startUrl":"<vedi sotto>"}}]}
aura.context = {"mode":"PROD","fwuid":"<fwuid>","app":"siteforce:loginApp2",
                "loaded":<loaded>,"dn":[],"globals":{},"uad":true}
aura.pageURI = <path+query della pagina di login su cui si è atterrati>
aura.token   = null            (letterale, stringa "null" - vedi sotto)
```

Header aggiuntivi necessari: `X-SFDC-Page-Scope-Id` (UUID generato
client-side, mai fornito dal server — un client headless deve generarne
uno proprio), `Origin`, `Referer` (pagina di login), `Content-Type`
esplicito.

> [!WARNING]
> **`startUrl` deve contenere il token `source=...`.** Il valore corretto
> non è un path nudo (`/PortaleClienti/setup/secur/RemoteAccessAuthorizationPage.apexp`)
> ma il valore decodificato del parametro `startURL` della pagina su cui
> si è atterrati al passo 1 — contiene un token generato dal server al
> primo hop che il server pretende di riavere indietro per validare che
> il login appartenga allo stesso flusso di autorizzazione. Senza,
> stesso `AuraClientInputException` generico di cui sopra.

> [!NOTE]
> **`aura.token` è la stringa letterale `"null"`.** Non un token vero: il
> client invia proprio il testo `null`, e il server lo accetta per questa
> azione specifica (non c'è ancora una sessione autenticata da proteggere
> via CSRF a questo punto del flusso). Due ipotesi precedenti — un valore
> nel body HTML, poi un valore in un cookie `__Host-ERIC_PROD*` — erano
> entrambe sbagliate: quel cookie non compare mai in una sessione fresca.

> [!NOTE]
> La pagina di login carica anche una **reCAPTCHA invisibile** di Google.
> Non richiede gestione: una richiesta `loginUser` reale riuscita non
> include alcun parametro reCAPTCHA, quindi il server non la valida per
> questa azione specifica.

Risposta di successo: `{"actions":[{"state":"SUCCESS","returnValue":
"OK:https://.../secur/frontdoor.jsp?..."}]}`.

### 3. `GET <frontdoor.jsp da returnValue>`

Stabilisce il cookie di sessione `sid`. La risposta **non è il form
OTP**: è una pagina-ponte che fa un redirect **JavaScript**
(`window.location.replace(...)`), non un redirect HTTP — un browser lo
segue automaticamente, un client headless no.

> [!WARNING]
> **Il vero form OTP è dietro un redirect JS, non nella risposta di
> frontdoor.jsp.** Va estratto l'URL dentro `window.location.replace(...)`
> (assoluto e già correttamente percent-encoded, nessun problema di
> doppia codifica qui) e seguito con una GET esplicita.

### 4. `GET <url estratto dal redirect JS>` → form OTP (Visualforce)

Pagina Visualforce classica con campi hidden `com.salesforce.visualforce.ViewState`
/`ViewStateVersion`/`ViewStateMAC`/`ViewStateCSRF`, il campo `OTP_Input`, e
un campo doppio per `Richiedi_nuovo_OTP`.

> [!WARNING]
> **Due campi con lo stesso nome parziale.** `Richiedi_nuovo_OTP` compare
> come una checkbox visibile (`element___input____Richiedi_nuovo_OTP`) E
> come un campo nascosto che la specchia via `onclick` JS
> (`element___hidden____Richiedi_nuovo_OTP`) — è **quest'ultimo** che va
> sottomesso. Un'estrazione generica trova per prima la checkbox (appare
> prima nell'HTML): il server interpreta la sottomissione come
> un'ennesima richiesta di reinvio invece che una convalida del codice.

### 5. `POST {LOGINFLOW_URL}?sfdcIFrameOrigin=null` (trigger invio OTP)

> [!NOTE]
> **Il codice OTP non parte da solo al caricamento della pagina.** Serve
> un primo submit del bottone (`nextAjax`), **senza** codice, per farlo
> davvero inviare via email/SMS. La risposta contiene testualmente
> "Abbiamo inviato un codice a 5 cifre al tuo indirizzo email" e include
> `ViewState`/CSRF **freschi**, da usare per il submit successivo (sembra
> ruotare ad ogni submit del form).

### 6. `POST {LOGINFLOW_URL}?sfdcIFrameOrigin=null` (submit OTP)

Stessi campi del passo 5 più `OTP_Input` col codice e
`Richiedi_nuovo_OTP` (hidden) a `false`. Risposta di successo: un meta
tag `<meta name="Location" content="...">` con l'URL successivo
(percent-encoded), che porta infine a `eneldist://redirect?code=...&state=...`
— mai seguito realmente, il `code` (e `state`, per la validazione CSRF)
vengono estratti direttamente dal testo della risposta.

### 7. `POST {OAUTH_TOKEN_URL}` (scambio code → token)

`grant_type=authorization_code` con `code`, `code_verifier` (PKCE),
`client_id`, `redirect_uri`. Risposta: `access_token`/`refresh_token`
standard OAuth2.

### Refresh

`POST {OAUTH_TOKEN_URL}` con `grant_type=refresh_token`. Nessun OTP
coinvolto — il percorso normale per ogni avvio/rinnovo. Salesforce non
restituisce sempre un nuovo `refresh_token` (va gestita la rotazione solo
quando presente, non assunta ad ogni chiamata).

## API dati (MuleSoft)

Header comuni: `Authorization: Bearer <access_token>`, `Method_User: <nome
metodo>` (varia per endpoint).

- **`POST /xs/misure/getSupplies`**: elenco POD dell'account. Richiede un
  corpo JSON vero (`json={}`), non solo il `Content-Type` dichiarato senza
  corpo — altrimenti risponde `500` generico ("Generic Error") invece di
  un errore di validazione chiaro. Con solo `Content-Type` mancante del
  tutto, risponde `415 Unsupported Media Type`.
- **`GET /xs/misure/reading`**: letture ufficiali cumulative per fascia
  (`publishedSlots`, magnitude `EA`/`ER`, `slotId` T1-T6) + picchi di
  potenza (`publishedPowerPeaks`, magnitude `POT`). Le fasce non attive
  sul contratto hanno `"value": null` (chiave presente, valore nullo — un
  `.get("value", 0)` non lo intercetta, serve gestirlo esplicitamente).
- **`GET /xs/misure/querydailyloadprofile`**: curva di carico. Accetta
  `rangeDateFrom`/`rangeDateTo` **davvero come intervallo**, non solo
  ripetendo lo stesso giorno due volte — confermato funzionante fino a
  181 giorni (~6 mesi) in un'unica risposta, cambio ora legale di marzo
  incluso e gestito correttamente lato server (vedi sotto). Un `404` su
  questo endpoint per una data recente è probabile significhi "dato non
  ancora pubblicato", non un errore — trattato dal client come
  equivalente a una risposta vuota, non come eccezione fatale.

### Struttura della risposta di `querydailyloadprofile`

```json
{
  "data": [
    {
      "readings": {
        "energyType": "A1",
        "sampleDate": "20260801",
        "sampleValues": [{"id": "1", "val": "0.359"}, "...", {"id": "96", "val": "0.020"}]
      },
      "sampleFrequency": 15,
      "timeType": "CONS",
      "initialSample": "2026-07-31T22:00:00.000+00:00"
    }
  ]
}
```

- Un elemento per giorno richiesto, 96 campioni da 15 minuti ciascuno.
- `initialSample` è il timestamp **UTC assoluto e completo** del campione
  `id=1`: il timestamp di ogni campione si ottiene sommando
  `(id-1) * sampleFrequency` minuti — nessuna interpretazione di flag per
  il cambio ora richiesta (a differenza del CSV Duereti/Unareti, vedi
  `pcf-protocol.md`). Verificato che il server sposta correttamente
  l'offset di `initialSample` nel giorno esatto del cambio ora legale di
  marzo, senza bisogno di logica dedicata lato client.
- **Confermato con dati reali** (21/08/2026): `val` è energia in kWh per
  intervallo, non potenza media in kW. Verificato confrontando il totale
  della curva per un mese intero con il delta di due letture ufficiali
  consecutive: 134.925 kWh in entrambi i casi, combacianti fino alla
  terza cifra decimale.
- `timeType` osservato finora: solo `"CONS"` (consumo). Un POD con
  impianto di produzione (`HasPlant: true` in `getSupplies`) potrebbe
  restituire un valore diverso (es. `"PROD"`) non ancora gestito.

## Limitazioni note / domande ancora aperte

- **Ritardo di pubblicazione dati**: non documentato ufficialmente.
  Osservazioni reali (21/08/2026): alle 00:01-01:00 il giorno
  immediatamente precedente non è ancora disponibile (`404`), quello di 2
  giorni prima sì; alle 18:00 dello stesso giorno il giorno precedente
  risulta invece già disponibile. Coerente con un ritardo di un giorno
  (stesso comportamento noto di Duereti/Unareti), ma su un numero di
  osservazioni molto minore. Il coordinator usa una coda di retry che
  rende il meccanismo robusto indipendentemente dal ritardo reale.
- **Refresh token nel tempo**: un primo test a poche ore di distanza dal
  login originale ha confermato che funziona e che il token non ruota ad
  ogni uso. Non ancora testato su settimane/mesi.
- Tutto il parsing HTML/regex sopra resta **best-effort**: se Enel cambia
  qualcosa lato loro (già successo più volte durante lo sviluppo — vedi
  la cronologia dei commit di `auth.py`), è probabile che si rompa di
  nuovo. `scripts/verify_edistribuzione_login.py` è lo strumento più
  rapido per verificare se il login funziona ancora, senza passare da
  Home Assistant.
