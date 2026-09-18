# Ireti — protocollo e design (supportato dalla v0.7.0)

Ireti (`smartpod.ireti.it`) **è supportato**: login, POD scoperti
dall'account e curva di carico a 15 minuti importata come external
statistics con una finestra scorrevole giornaliera (nessun cursore/coda
persistito — vedi [Design del coordinator](#design-del-coordinator)).
L'endpoint di misura è **confermato con dati reali**:
[issue #6](https://github.com/riccardorossi92/HomeAssistant-Contatore/issues/6)
(russomichele, 17/09/2026) ha un POD attivo e ha condiviso sia il report
dello script sia la cattura diretta di `measures-loadprofiles` dal
browser.

> A differenza di **Areti** (portale di sole pratiche, nessun dato di
> misura — vedi [areti-protocol.md](areti-protocol.md)), qui il backend
> di misura esiste, risponde, ed è più granulare degli altri distributori
> supportati: curva a 15 minuti invece che giornaliera/mensile.

**Non ancora testato ufficialmente dentro Home Assistant**: nessuna
installazione reale lo ha ancora usato in produzione (config flow, import
automatico, sensori). Alcune ipotesi restano da confermare con l'uso
reale — vedi [Cosa resta aperto](#cosa-resta-aperto). Se hai una
fornitura Ireti attiva, puoi aiutare — vedi
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

## Endpoint di misura

| Endpoint | Metodo | Payload | Cosa restituisce |
|---|---|---|---|
| `/readings/exabeat/measures-loadprofiles` | POST | vedi sotto | **CONFERMATO con dati reali** — `loadProfiles[]`, curva a 15 min |
| `/users/exabeat/history` | POST | `{customerTaxCodeVat, pod, startDate, endDate}` | `customerPodActive` (bool), `podType` (`orario` / `fasce` / `mono orario`) — non ancora confermato con dati reali |
| `/users/exabeat/registry` | POST | idem | anagrafica tecnica del POD — non ancora confermato |
| `/readings/exabeat/measures` | POST | `{customerTaxCodeVat, pod, startDate, endDate}` | letture periodiche per fascia (`energyActiveF1/F2/F3`, ecc.) — non ancora confermato |
| `/readings/exabeat/can-request-verify` | POST | `{...}` | se è possibile richiedere una verifica misuratore — non ancora confermato |
| `/readings/selfreading/save`, `/readings/selfreading/checkotp` | POST | — | autolettura (con OTP) — non serve per l'integrazione |

Gli endpoint non confermati restano ricavati dal codice del bundle
(vedi cronologia del file); non sono indovinati, ma non hanno ancora
una risposta reale a fronte.

### `measures-loadprofiles` — confermato con dati reali (17/09/2026)

Da [issue #6](https://github.com/riccardorossi92/HomeAssistant-Contatore/issues/6),
cattura diretta dal browser (non dallo script nella sua versione di
allora — vedi [nota sul fix dello script](#nota-il-fix-dello-script-18092026)):

```
POST /readings/exabeat/measures-loadprofiles
Authorization: Bearer <access_token>

{
  "operation": "PRELIEVO",
  "podType": "orario",
  "startDate": "2026-07-31T22:00:00.000Z",
  "endDate": "2026-08-31T21:59:59.000Z",
  "customerTaxCodeVat": "<codice fiscale o P.IVA>",
  "pod": "IT020E..."
}
```

Risposta (un mese, un `loadProfiles[]` per giorno):

```json
{
  "responseMessage": null,
  "reponseCode": null,
  "requestId": "285",
  "errorCode": null,
  "pod": "IT020E...",
  "readings": null,
  "intakeReadings": null,
  "loadProfiles": [
    {
      "loadProfileDate": "20/08/2026 00:00:00 +0200",
      "serialNumber": "...",
      "timeType": "",
      "energyType": "A1",
      "measType": "0",
      "sampleValues": [0.023, 0.022, 0.018, "... 96 valori totali"]
    }
  ]
}
```

Osservazioni:

- **96 `sampleValues` per giorno** (15 minuti l'uno) nel report reale —
  non 92 come ipotizzato dal solo codice del bundle: la cattura non
  cadeva su un giorno di cambio ora legale, quindi lo splice a 96 non
  serviva. Da verificare il comportamento nei giorni di cambio ora.
- Valori nell'ordine di `0.01`–`0.03`: sommando i 96 campioni di un
  giorno si ottiene ~1.9, plausibile come **kWh per intervallo di 15
  minuti** (giorno a consumo basso/standby) più che come potenza
  istantanea in kW — ma non ancora confermato incrociando con un totale
  giornaliero noto.
- `endDate`/`startDate` nella richiesta sono UTC con l'offset già
  applicato (`21:59:59Z` = `23:59:59` ora locale CEST); `loadProfileDate`
  nella risposta invece è locale con offset esplicito (`+0200`).
- `energyType: "A1"` ed `measType: "0"` non ancora mappati a un
  significato certo (probabile: energia attiva, fascia F1 o
  "monoraria"); `podType: "orario"` nella richiesta è quello letto da
  `history`/`registry` per quel POD.
- `operation: "PRELIEVO"` suggerisce che esista anche `"IMMISSIONE"` per
  POD di produzione (fotovoltaico) — da verificare, non testato.

### Nota: il fix dello script (18/09/2026)

Fino a [issue #6](https://github.com/riccardorossi92/HomeAssistant-Contatore/issues/6),
`scripts/raccogli_dati_ireti.py` non trovava/provava
`measures-loadprofiles` da solo, per due bug nello script (non nell'API) —
la cattura che ha risolto il caso è stata fatta a mano dal browser, non
dallo script:

1. Il regex che estrae i path dal bundle cercava solo prefissi
   `users|misure|pod|prelievi|consumi|api` — `/readings/exabeat/...` non
   c'entrava e veniva scartato.
2. Anche trovandolo, lo script lo avrebbe provato con `GET` e query string
   (`pod`, `dataDa`, `dataA`), ma l'endpoint vuole `POST` con body JSON
   (`operation`, `podType`, `startDate`, `endDate`, `customerTaxCodeVat`,
   `pod`).

Entrambi risolti: il regex ora include `readings`, e i quattro endpoint
`exabeat`/`readings` noti vengono provati esplicitamente con `POST` e il
body corretto (incatenando il `podType` letto da `history`), non più con
la scansione generica GET+query.

## Associazione del POD

È un'azione self-service, non un limite tecnico:
`POST /users/pods/save` con `{code, name, consumerId, companyId}` —
niente OTP né numero bolletta nel payload; la validazione (match
intestatario ecc.) è lato backend, che risponde `reponseCode: 300` se
l'associazione riesce. Si fa dal portale: **"Aggiungi POD" → codice POD**.
L'informativa privacy del portale indica il POD come dato richiesto in
fase di registrazione, quindi un account Ireti "normale" dovrebbe avere
già il POD associato — quello di cattura è un caso anomalo.

## Design del coordinator

Implementazione: `custom_components/contatore_letture/distributors/ireti/`
(stesso schema a 7 file di `distributors/areti/`: `const.py`, `auth.py`,
`api.py`, `coordinator.py`, `statistics.py`, `sensor.py`, `__init__.py`).

**Finestra scorrevole, non cursore/coda.** A differenza di E-Distribuzione
(una chiamata = un giorno, serve una coda per tracciare cosa manca) e di
Areti (una chiamata = un mese intero, disponibile solo a mese chiuso,
serve un cursore), `measures-loadprofiles` accetta un range di date
arbitrario e restituisce un `loadProfiles[]` con un elemento per ogni
giorno disponibile in quel range (confermato: un mese intero in una sola
chiamata). Quindi: **nessuno stato persistito** — ogni ciclo (una volta al
giorno) si richiede una finestra degli ultimi `FINESTRA_GIORNI_DEFAULT`
giorni (14, `const.py`) e si importa tutto quello che torna. Un giorno
pubblicato in ritardo rientra da solo al ciclo successivo — non è noto
il ritardo di pubblicazione reale, ma non serve conoscerlo: la finestra
lo assorbe, purché sia abbastanza larga.

**Credenziali salvate, login rifatto ad ogni ciclo.** Il `refresh_token`
dura solo 30 minuti (inutilizzabile con un ciclo giornaliero/orario):
si salvano username/password sull'entry (come `pcf_common`, non come
E-Distribuzione, il cui refresh_token Salesforce è invece durevole) e si
rifà il password-grant ad ogni ciclo. Credenziali non valide sollevano
`ConfigEntryAuthFailed` (reauth automatico di Home Assistant), non un
semplice `UpdateFailed`.

**`podType` con ripiego.** Necessario nel payload di
`measures-loadprofiles`, si legge da `/users/exabeat/history` —
endpoint **mai confermato con dati reali** (vedi sopra). Se fallisce o
non lo contiene, si ricade su `"orario"` (l'unico valore osservato
finora) con un avviso nei log una tantum per POD, invece di far fallire
l'intero ciclo: un'ipotesi sbagliata qui produce nella peggiore delle
ipotesi un payload che il portale rifiuta esplicitamente
(`IretiApiError` → `UpdateFailed`), non un dato silenziosamente sbagliato.

**Solo `energyType: "A1"` viene importato.** Non è noto se un POD
`"fasce"`/`"mono orario"` restituisca più elementi per giorno con
`energyType` diversi per fascia: sommare alla cieca rischierebbe di
mescolare grandezze eterogenee, quindi si importa solo l'energia attiva
("A1", l'unico tipo osservato) e si scarta (con un log) qualunque altro
valore — un sottoinsieme sicuramente corretto piuttosto che un totale
potenzialmente sbagliato.

## Cosa resta aperto

Non blocca l'uso, ma va tenuto d'occhio quando arriva un riscontro reale:

- **Unità di `sampleValues`**: assunta kWh per intervallo di 15 minuti
  (somma giornaliera ~1.9 nell'unico esempio reale, plausibile per un
  consumo di base) — non confermata al 100%, potrebbe essere potenza
  media in kW. Se sbagliata, si corregge in un punto solo
  (`statistics.py`, moltiplicando per 0.25): l'import è idempotente,
  ricalcola sempre la somma progressiva da zero.
- **`podType` diverso da `"orario"`**: mai osservato. Potrebbe cambiare
  la forma di `loadProfiles` (più elementi per giorno, per fascia).
- **Giorni di cambio ora legale**: il codice del bundle JS ipotizza 92
  campioni invece di 96 (vs 96 confermati su un giorno normale) — mai
  verificato con un giorno di cambio ora reale.
- **`/users/exabeat/history`, `/users/exabeat/registry`,
  `/readings/exabeat/measures`**: mai confermati con dati reali, solo
  ricavati dal codice del bundle. Il coordinator dipende da `history` solo
  per `podType`, con un ripiego se fallisce (vedi sopra) — `registry` e
  `measures` non sono usati.
- **Range più corti di un mese** per `measures-loadprofiles`: l'unico
  esempio reale è un mese intero. Il coordinator li usa comunque (finestra
  di 14 giorni) partendo dal presupposto ragionevole che l'API accetti
  range arbitrari (è solo `startDate`/`endDate` ISO) — non ancora
  verificato.
- **POD di produzione** (`operation: "IMMISSIONE"` invece di
  `"PRELIEVO"`): non supportato, mai testato.

## Come contribuire

Il dato principale (`measures-loadprofiles`, curva a 15 min) è già
confermato — vedi sopra. Il modo più utile di aiutare ora è **usare
l'integrazione con un account Ireti reale** e segnalare cosa non torna
(vedi [Cosa resta aperto](#cosa-resta-aperto)), non più raccogliere dati
a mano.

Se hai una **fornitura Ireti attiva e almeno un POD associato**, c'è uno
script che fa tutto da solo (corretto il 18/09/2026 — vedi
[nota sul fix](#nota-il-fix-dello-script-18092026)):

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
4. prova con POST i quattro endpoint di misura confermati/noti
   (`exabeat/history`, `exabeat/registry`, `readings/exabeat/measures`,
   `readings/exabeat/measures-loadprofiles`) con il body corretto sul tuo
   POD;
5. prova anche gli altri path del bundle che sembrano di misura, come
   esplorazione, e ne registra la struttura delle risposte.

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
