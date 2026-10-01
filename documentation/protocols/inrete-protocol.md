# Inrete Distribuzione — "Portale Hera 105", self-service per BT (supporto non ancora implementato)

**Inrete Distribuzione Energia S.p.A.** (gruppo **Hera**, distribuisce
elettricità e gas in **Emilia-Romagna e Toscana**) — sede a Bologna.

> [!NOTE]
> **Correzione (01/10/2026)**: una versione precedente di questa scheda
> (13/09/2026) concludeva "non fattibile" per il cliente BT, rimandato
> solo al Portale Consumi ARERA o al venditore. Non era un errore di
> lettura: secondo l'utente, che ha seguito la pagina nel tempo, quello
> era davvero l'unico canale indicato all'epoca per la BT — la sezione
> **"PORTALE HERA 105"** (sotto) è stata aggiunta dopo, coerente con il
> footer della pagina attuale ("aggiornata al 15/09/2026", due giorni
> dopo la prima lettura). Non verificabile su Wayback Machine (nessuno
> snapshot disponibile per quella pagina in quel periodo), ma resta
> l'ipotesi più probabile. Verdetto quindi legato alla **data di
> lettura**, non a un difetto del processo di ricerca — vale comunque
> ricontrollare periodicamente le pagine pubbliche di un distributore già
> scartato, visto che possono cambiare.

Stato: **non ancora supportato, ma promettente**. Login verificato
(pagina pubblica raggiunta, nessuna credenziale reale provata); non
ancora noto se dopo la registrazione il portale esponga davvero dati di
misura (curva di carico) o solo un'anagrafica/altro. Stesso tipo di
incognita già visto per [SET Distribuzione](set-distribuzione-protocol.md)
e [AcegasApsAmga](acegasapsamga-protocol.md).

Fonte: pagina pubblica
<https://www.inretedistribuzione.it/energia-elettrica/clienti-finali-e-tecnici/misure-periodiche-mensili>
(letta 01/10/2026, "Pagina aggiornata al 15/09/2026") + verifica diretta
degli endpoint OIDC pubblici del portale di login (nessun account creato,
nessuna credenziale inserita).

## Verdetto

| | |
|---|---|
| Cliente finale BT (utenza domestica) ha un portale self-service? | **Sì** — **"PORTALE HERA 105"**, registrazione **"Iscrizione Immediata"** direttamente nel portale, nessuna PEC/modulo cartaceo richiesto per il BT (a differenza dell'MT, vedi sotto) |
| Dati richiesti per la registrazione | Nome, cognome, codice fiscale, email, **POD**, documento di riconoscimento, documento di delega (solo se il titolare della consultazione è diverso dal titolare del POD) |
| Meccanismo di login | **Azure AD B2C** (Microsoft), MSAL.js, Authorization Code + PKCE — stesso schema di [SET Distribuzione](set-distribuzione-protocol.md) e [AcegasApsAmga](acegasapsamga-protocol.md), ma tenant/app diversi da entrambi |
| Dati di misura disponibili? | **Non ancora verificato** — nessun account registrato, nessuna cattura oltre la pagina di login pubblica |
| Protocollo PCF (Duereti/Unareti)? | No — architettura completamente diversa |
| Utenti MT | Canale separato: `portale.inretedistribuzione.it` + registrazione via `https://e-cdn-mcont-weu-pr-001-registrazionemt.azureedge.net/` (energia reattiva immessa, Delibera 232/2022/R/EEL — non il caso d'uso di questa integrazione) |
| Produttori da fonti rinnovabili | Passano dal **GSE** (`auth.gse.it`) — fuori scope |

## Quadro generale

La pagina pubblica distingue quattro canali. Il primo paragrafo
("Clienti Finali in solo Prelievo" → Portale Consumi ARERA/venditore) è
la via generica suggerita a chi vuole solo *consultare* i propri consumi
tramite SPID/CIE — **ma più sotto nella stessa pagina**, la pagina
descrive canali di **registrazione diretta presso Inrete**, distinti per
tensione di fornitura:

1. **MT** (media tensione): portale Inrete per il monitoraggio
   dell'energia reattiva immessa in rete (obbligo normativo specifico),
   con registrazione self-service separata su
   `e-cdn-mcont-weu-pr-001-registrazionemt.azureedge.net`.
2. **BT** (bassa tensione — il caso che interessa questa integrazione):
   **"PORTALE HERA 105"**, con registrazione self-service
   ("Iscrizione Immediata") richiedente nome/cognome, codice fiscale,
   email, POD e documento d'identità (+ delega se il richiedente non è
   il titolare del POD).
3. **Produttori**: GSE.

rientra nel **caso B** di [`CONTRIBUTING.md`](../CONTRIBUTING.md)
(protocollo strutturalmente nuovo, non PCF).

## Login — pagina pubblica verificata (01/10/2026), nessuna credenziale provata

Flusso Azure AD B2C "sign up or sign in" via MSAL.js (Authorization Code
+ PKCE), identico nella forma a quello di SET Distribuzione:

```
AUTH_DOMAIN  = portalehera2gwebprodb2c.b2clogin.com
TENANT       = portalehera2gwebprodb2c.onmicrosoft.com (tenant id 89c2880e-bdef-4820-ad7e-956f6ff026b5)
POLICY       = b2c_1a_signup_signin
CLIENT_ID    = b3c6c162-bb68-4e9c-9c13-88e660e145da
REDIRECT_URI = https://e-cdn-2gweb-weu-pr-001-inretedistribuzioneenergia.azureedge.net
SCOPE        = openid profile offline_access
company_id   = 1900  (query param passato all'authorize — ipotesi: piattaforma "2G Web" condivisa tra più distributori/brand, col brand selezionato da company_id; non confermato)
```

La pagina di login (titolo `PORTALE HERA 105`) è raggiungibile
pubblicamente senza credenziali ed espone: campo login + "Password
dimenticata?" + link **"Iscrizione immediata"** per chi non ha un
account. Localizzata in italiano/inglese/slovacco (lingue disponibili
nel selettore, osservato direttamente — non confermato se rilevante per
l'operatività del gruppo).

`GET {AUTH_DOMAIN}/{TENANT}/b2c_1a_signup_signin/v2.0/.well-known/openid-configuration`
(endpoint pubblico, nessuna autenticazione) espone, tra l'altro:

```json
"claims_supported": [
  "name", "given_name", "family_name", "signInNames.emailAddress",
  "extension_codiceFiscale", "POD", "sub", "tid",
  "extension_termsOfUseConsentAcceptedOn", "validated", "soc",
  "extension_isPF", "iss", "iat", "exp", "aud", "acr", "nonce", "auth_time"
]
```

> [!IMPORTANT]
> `POD` ed `extension_codiceFiscale` sono **claim del token B2C stesso**
> (custom attribute del profilo utente), non recuperati via una chiamata
> API separata dopo il login. Insieme al claim `validated`, questo
> conferma che la registrazione lega codice fiscale + POD con una
> verifica (presumibilmente manuale, vista la richiesta del documento
> d'identità) **prima** che l'account diventi utilizzabile — "Iscrizione
> Immediata" è probabilmente il nome dello step di richiesta, non una
> promessa che l'accesso ai dati sia istantaneo. Da verificare quanto
> tempo passa tra la registrazione e la prima disponibilità reale del
> POD nel token.

## Cosa manca

1. **Un account registrato** (via "Iscrizione Immediata", con un POD
   Inrete BT reale) per sapere:
   - quanto tempo serve perché la registrazione venga validata;
   - se dopo il login il frontend (servito da
     `e-cdn-2gweb-weu-pr-001-inretedistribuzioneenergia.azureedge.net`)
     espone API JSON per i dati di misura, e con che granularità (curva
     di carico a 15'/oraria, o solo letture mensili come il nome pagina
     "misure periodiche mensili" suggerisce);
   - se un account con più di un POD può consultarli tutti dallo stesso
     login (rilevante per il caso d'uso dell'integrazione).
2. Una cattura **Chrome o Firefox** (non Safari, vedi nota WebKit redige
   gli header `Authorization` — stesso problema già documentato per
   [SET Distribuzione](set-distribuzione-protocol.md)) di login completo
   + pagina misure con un dato caricato.
3. Da confermare se `company_id=1900` è davvero un selettore di brand su
   una piattaforma "2G Web" condivisa con altri distributori — utile
   saperlo se in futuro si guarda a un altro distributore con lo stesso
   vendor.

## Come contribuire

Serve chi ha una fornitura Inrete **in bassa tensione** (il caso
residenziale tipico) e vuole provare la registrazione "Iscrizione
Immediata" sul Portale Hera 105
(<https://www.inretedistribuzione.it/energia-elettrica/clienti-finali-e-tecnici/misure-periodiche-mensili>,
sezione BT). Una volta ottenuto l'accesso, serve una cattura HAR
(Chrome/Firefox, Preserve log attivo *prima* di navigare, "Export HAR
with content") di login + pagina misure con un grafico/dato caricato.

Vedi [Aiutare senza scrivere codice](../CONTRIBUTING.md#aiutare-senza-scrivere-codice-raccolta-dati)
in CONTRIBUTING.md: **non allegare la HAR grezza a una issue
pubblica** (contiene token di sessione, codice fiscale e dati personali
in chiaro) — apri prima una issue per concordare come condividerla.
