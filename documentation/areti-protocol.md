# Areti — protocollo (implementato)

Areti (distributore di Roma e Formello, gruppo ACEA) **è supportato** da
`distributors/areti/`. La ricerca precedente (v. cronologia git di questo
file) che concludeva "non fattibile" era basata su un account **senza
POD associato**: mancavano le pagine e i componenti di misura, non solo
i dati. Catture successive (04/09/2026) da un account **con POD attivo**
hanno trovato l'endpoint che restituisce la curva di carico a quarto
d'ora in kWh con dati reali, e una cattura del **login da zero** (stesso
giorno) ne ha verificato il meccanismo — nessun OTP. Questa scheda resta
come riferimento tecnico del protocollo (endpoint, gotcha, struttura
delle risposte), sul modello di `pcf-protocol.md`/
`edistribuzione-protocol.md`.

## Verdetto

| | |
|---|---|
| Portale distributore espone consumi/curve di carico? | **Sì** — curva a 15 minuti + aggregato giornaliero, verificato con dati reali (agosto 2026) |
| Meccanismo di login | **Verificato** — email/password su form Visualforce/JSF + ticket-exchange Salesforce, **nessun OTP** osservato |
| Implementazione | **Fatta**: `distributors/areti/` (auth.py/api.py/coordinator.py/sensor.py/statistics.py), agganciata a config_flow/reauth/options/recupera_storico. Test in `tests/areti/`. |

## Quadro generale

Confermato su due catture reali (04/09/2026, un account senza POD e uno
con POD attivo):

| | |
|---|---|
| Area riservata | `https://areariservataclienti.areti.it/portaleareti/s/` |
| Piattaforma | Salesforce Experience Cloud, framework **Aura** |
| Org ID | `00D5I000000D0zj` |
| Namespace custom | `ARIA_` |
| Auth | sessione a cookie Community (`sid`, `sid_Client`) + `aura.token` CSRF (letto da un cookie dal nome offuscato, non fisso) + `fwuid` (id build framework, cambia a ogni release Salesforce) |
| Endpoint dati | `POST /portaleareti/s/sfsites/aura`, corpo form-encoded con `message`/`aura.context`/`aura.token` |

## Gotcha TLS: il server non manda il certificato intermedio

Verificato con `openssl s_client -showcerts` (04/09/2026), ripetuto su
tutti e 3 gli IP edge dietro cui gira il sito (infrastruttura
Salesforce, `*.edge2.salesforce.com`): **il server manda in handshake
solo il proprio certificato**, non l'intermedio (`DigiCert Global G2 TLS
RSA SHA256 2020 CA1`) che serve a completare la catena fino alla radice
attendibile. Il certificato del sito di per sé è valido (CA reale,
hostname corretto, non scaduto) — è la catena servita a essere
incompleta, sistematicamente, non un caso isolato.

Un client che fa "AIA chasing" (recupera da solo l'intermedio mancante,
o lo ha già in cache da altri siti DigiCert — comportamento tipico di
browser e di molte build di OpenSSL/LibreSSL moderne) non se ne accorge.
Un client "nudo" senza quella cache — verificato su un Python appena
installato da python.org su macOS — fallisce con
`SSLCertVerificationError: unable to get local issuer certificate`,
anche con `certifi` aggiornato (che porta solo le CA radice, non gli
intermedi). **Non è un problema del client locale né un certificato del
sito non valido: è il server che non completa la catena.**

Fix verificato: aggiungere esplicitamente l'intermedio mancante (scaricabile
dall'URL pubblico "CA Issuers" nel certificato stesso,
`cacerts.digicert.com` — non è un segreto) al bundle di CA usato per la
verifica. Implementato in
[`scripts/verify_areti_login.py`](../scripts/verify_areti_login.py).
Vale anche per l'implementazione finale: se `distributors/areti/auth.py`
userà `aiohttp` (come `edistribuzione/`), lo stesso problema può
presentarsi in un ambiente Home Assistant minimale (es. container Docker
senza cache di intermedi) — va portato lì lo stesso fix.

## Login — verificato (04/09/2026), nessun OTP

Non è il login standard "Salesforce Community" (`POST .../s/login`):
Areti mette davanti al sito Lightning una vecchia pagina di login
**Visualforce/JSF** (si vede da `ajax4jsf`, `VFState.js`, ids di campo
tipo `loginPage:loginForm:...`). Tre passaggi, tutti verificati su
cattura reale con credenziali vere:

1. **`GET /portaleareti/AretiLoginURL`** — HTML della pagina di login.
   Contiene 3 campi nascosti **da rileggere ad ogni tentativo** (cambiano
   per ogni caricamento pagina, sono anti-tampering, non hardcodabili):
   `com.salesforce.visualforce.ViewState`, `…ViewStateVersion`,
   `…ViewStateMAC`.
2. **`POST /portaleareti/AretiLoginURL?refURL=<url target dopo login>`**
   — form-urlencoded:
   ```
   AJAXREQUEST=_viewRoot
   loginPage:loginForm=loginPage:loginForm
   loginPage:loginForm:login-email=<email>
   loginPage:loginForm:login-password=<password>
   com.salesforce.visualforce.ViewState=<dal passo 1>
   com.salesforce.visualforce.ViewStateVersion=<dal passo 1>
   com.salesforce.visualforce.ViewStateMAC=<dal passo 1>
   loginPage:loginForm:j_id3=loginPage:loginForm:j_id3
   ```
   Risposta: `200`, corpo vuoto (risposta parziale Ajax4jsf), ma header
   **`Location`** con l'URL del passo 3 (contiene un ticket `cshc=…`) e
   `Set-Cookie: oinfo=…`. **Nessun passaggio OTP/MFA** in questa cattura.
3. **`GET` dell'URL nell'header `Location`** —
   `/portaleareti/secur/frontdoor.jsp?allp=1&appkp=1&apv=1&cshc=<ticket>&refURL=…`,
   il classico ticket-exchange Salesforce (`frontdoor.jsp`). Qui vengono
   impostati i cookie di sessione veri: `sid`, `sid_Client`, `clientSrc`,
   `inst`, `oid`, `__Secure-has-sid`.

Da qui, `GET /portaleareti/s/` con questi cookie carica l'area riservata
loggata. La pagina porta un blob JSON inline con `fwuid` e con l'id
`"loaded":{"APPLICATION@markup://siteforce:communityApp":"<id>"}`
(serve nel corpo di ogni chiamata Aura, vedi sotto).

**Come si ottiene `aura.token` — non è ovvio.** Non è nel corpo della
pagina: lo stesso blob JSON porta una chiave `"eikoocnekot":"<nome
cookie>"` — `eikoocnekot` letta al contrario è `tokencookie` (un trucco
di offuscamento noto del framework Aura). Il suo **valore** è il *nome*
di un cookie impostato dalla stessa risposta (osservato
`__Host-ERIC_PROD-<numero>`, ma il prefisso non è detto resti fisso): il
**valore di quel cookie** è il vero `aura.token` da mandare in ogni
chiamata successiva. Verificato byte per byte su cattura reale (il
valore del cookie e il campo `aura.token` mandato nelle richieste
successive combaciano esattamente).

> [!NOTE]
> Il cookie-token ha `Max-Age=60` (secondi) nella risposta che lo
> imposta, e non risulta più reimpostato dalle chiamate Aura successive
> nella cattura disponibile (che copre solo ~3 secondi in tutto) — non è
> chiaro se dopo 60s serva ricaricare `/s/` per un token fresco o se il
> valore resti valido più a lungo lato server nonostante l'istruzione al
> browser di scartarlo. Da verificare con `scripts/verify_areti_login.py`
> su una sessione più lunga.

> [!NOTE]
> Non verificato: comportamento con credenziali errate (struttura
> dell'errore), se il portale richiede mai OTP in altre condizioni
> (nuovo dispositivo/IP — questa cattura è da un dispositivo già noto
> all'account), e la durata di `sid` prima che serva rifare login.

Tutto questo flusso è implementato (per verifica, non ancora nel modulo
vero) in
[`scripts/verify_areti_login.py`](../scripts/verify_areti_login.py).

## La catena di chiamate per la curva di un mese

Tutte via `POST .../s/sfsites/aura`, descriptor
`aura://ApexActionController/ACTION$execute`, con `classname`/`method`
diversi. In ordine, così come osservate nella navigazione reale
(login → Forniture di energia → dettaglio fornitura → tab "Dati di
misura"):

1. **`ARIA_InfoPodVenditori_WS.getInfoPod_WS`**`({POD, user})` — dati
   tecnici del POD (indirizzo, matricola contatore, potenza impegnata,
   dati del titolare in `DetailPod[]`). Usato per popolare la pagina
   dettaglio, non per la misura.
2. **`ARIA_dettagliFornituraController.getDetailTabLabels`**`()` —
   elenco dei tab della pagina dettaglio fornitura. Tra questi c'è
   **"Dati di misura"** (`Name: "DatiMisura"`) insieme a Dati POD, Dati
   contatore, Continuità, Interruzioni di energia, Rimborsi, ecc.
3. **`ARIA_dettagliFornituraController.getRegistrazionePuntiFrnitura`**`({pod})`
   — dati contrattuali (venditore, codice contratto, stato fornitura,
   tipo fornitura BT/MT/AT).
4. **`ARIA_DatiDiMisuraController.getAnni`**`()` — anni disponibili per
   la selezione (nella cattura: 2012–2026, statico, non dipende dal POD).
5. **`ARIA_DatiDiMisuraController.getConfigurations`**`({podName})` —
   la chiamata chiave di setup. Restituisce:
   - `codiceBP` — **codice Business Partner**, va passato alle chiamate
     successive (non è il POD né il codice fiscale, è un terzo
     identificativo);
   - `codiceFiscale` del titolare, richiesto anch'esso più avanti;
   - `is2G: true/false` — se il contatore è di seconda generazione
     (telelettura); la cattura è su un contatore 2G;
   - `unitOfMeasureMapping` — **`EA`/`UA` → `kWh`, `EI`/`EC`/`UI` →
     `kVarh`**;
   - `energyOptions` — le 5 componenti energetiche possibili: `EA`
     (energia attiva **entrante** = prelievo/consumo — quella che serve
     per la Energy Dashboard), `UA` (attiva **uscente**, per chi
     immette/produce), `EI`/`EC` (reattiva induttiva/capacitiva),
     `UI` (induttiva uscente);
   - `mappingDatiMisura` — 35 campi, combinazione di componente ×
     fascia oraria F1–F6 (es. `F1ActiveEN` = "Lettura fascia F1 energia
     attiva entrante") più i totali (`Tot_Active_EN`).
6. **`ARIA_DatiDiMisuraController.getComponentiRilevanti`**`({mese, anno, podName, misuraConfigurationJson})`
   — filtra `energyOptions` su quali hanno davvero dati per quel
   mese/anno (nella cattura, per un mese normale, risulta solo `EA`).
7. **`ARIA_DatiMisuraGetMisurazioni_WS.getMisurazioni`**`({inputParamsJson})`
   — **i dati veri**. `inputParamsJson` è una stringa JSON con:
   ```json
   {
     "useMock": false,
     "meseAnno": "MMYYYY",
     "codiceBP": "<da getConfigurations>",
     "codiceFiscale": "<da getConfigurations>",
     "pod": "<POD>",
     "componenteEnergia": "EA"
   }
   ```

## Struttura della risposta di `getMisurazioni`

La risposta Aura ha `returnValue.misureByBP` come **stringa JSON
annidata** (va fatto un secondo `json.loads`, non è già un oggetto). Una
volta parsata, per ogni Business Partner (di solito uno):

```jsonc
{
  "esitoPosizioneBP": {
    // LETTURE CUMULATIVE del contatore alla data di fine mese (non il
    // consumo del mese!) - per fascia e per componente, es.:
    "Tot_Active_EN": "4359.389…",   // lettura cumulativa totale (kWh) a DataLettFineM
    "F1ActiveEN": "1614.205…", "F2ActiveEN": "1057.246…", "F3ActiveEN": "1687.938…",
    "PiccoPotenzaEA": "1.028…",     // picco di potenza nel mese (kW)
    "DataLettFineM": "20260831",    // data della lettura, YYYYMMDD
    "ComponenteEnergia": "EA",
    "Bsnpart": "<codiceBP>"
  },
  "esitoBP": true,
  "bpCode": "…",
  "elementiCurve": [
    // CURVA DI CARICO A 15 MINUTI (consumo del mese, non cumulativo):
    // 31 giorni × 96 quarti d'ora = 2976 elementi
    {"Value": "0.034", "Ora": "00:00:00", "Data": "2026-08-01"},
    {"Value": "0.017", "Ora": "00:15:00", "Data": "2026-08-01"},
    // …
  ],
  "elementiAggregati": [
    // TOTALE GIORNALIERO (consumo del giorno): 1 elemento per giorno del mese
    {"Value": "2.427", "Ora": "23:59:59", "Data": "2026-08-01"},
    {"Value": "2.42",  "Ora": "23:59:59", "Data": "2026-08-02"},
    // …
  ]
}
```

> [!IMPORTANT]
> **`esitoPosizioneBP` e `elementiCurve` NON sono confrontabili** - sono
> due grandezze diverse per costruzione, verificato su dati reali
> (04/09/2026): `Tot_Active_EN` per agosto 2026 era `4359.389`, mentre la
> somma di tutti gli `elementiCurve` dello stesso mese era `96.517` (un
> fattore ~45×, non un errore di arrotondamento). La spiegazione:
> `esitoPosizioneBP` sono **letture cumulative del contatore** (come
> conferma sia il nome dei campi, "Lettura fascia F1…", sia il CSV che
> l'export del sito genera, intestato "Valori al 31/08/2026" - un valore
> "a quella data", non "in quel mese"), coerenti con una fornitura attiva
> da ~35 mesi (2023-09-06) e una media di ~125 kWh/mese. `elementiCurve`/
> `elementiAggregati` sono invece il consumo **del solo mese richiesto**.
> Per l'Energy Dashboard servono `elementiCurve`/`elementiAggregati`, non
> `esitoPosizioneBP` (che semmai è utile come sensore "lettura contatore
> a fine mese", sul modello delle letture periodiche PCF).

`Value` è in **kWh** (per `componenteEnergia: "EA"`, via
`unitOfMeasureMapping`). `elementiCurve` è esattamente il tipo di dato
che serve per l'import a curva di carico nella Energy Dashboard (stesso
livello di dettaglio delle curve Duereti/Unareti/E-Distribuzione);
`elementiAggregati` è il totale giornaliero, comodo come scorciatoia (ma
NON come controllo di coerenza contro `esitoPosizioneBP` - vedi sopra).

Per coprire un intervallo di più mesi serve ripetere il punto 7 una
volta per `meseAnno`, come per il resto: non risulta un parametro
"da-a" in un'unica chiamata.

## Disponibilità: il mese in corso non è mai presente

Verificato il 04/09/2026: `getMisurazioni` per il mese in corso
(settembre) ha risposto `esito: true` ma con `misureByBP` **una lista
JSON vuota** - non dati parziali per i primi giorni, proprio nessun
Business Partner. Agosto (mese chiuso) era invece già disponibile lo
stesso giorno (4 settembre).

Non è lo stesso comportamento di `RITARDO_DATI_GIORNI=1` dei PCF (che
accettano richieste su singolo giorno con ~1 giorno di ritardo, vedi
`pcf_common/const.py` - lì è il **coordinator** a scegliere comunque il
mese completo, non un limite dell'API): qui sembra proprio l'endpoint
Areti a lavorare per **mese solare chiuso**, senza dati intermedi
interrogabili durante il mese. Da riconfermare con un secondo test a
metà mese (per escludere che compaiano dati parziali più avanti nel
mese) e a inizio ottobre (per capire con quanti giorni di ritardo dopo
la chiusura il mese diventa disponibile - qui sappiamo solo "entro il
giorno 4", non il minimo).

## Design del coordinator (deciso, 04/09/2026)

Dato che Areti pubblica a mese solare chiuso (sopra) e non giorno per
giorno, il coordinator NON userà il pattern PCF ("chiedi sempre il mese
precedente rispetto a oggi", ricalcolato ogni volta). Userà invece un
**cursore persistito** che avanza solo quando trova dati, così recupera
da solo eventuali mesi saltati (es. HA spento per un periodo) invece di
poterne perdere uno per sempre:

1. Stato persistito sulla config entry: `mese_da_importare` (il più
   vecchio mese non ancora importato con successo).
2. Ogni ciclo del coordinator (**una volta al giorno**, come i PCF -
   controllare più spesso non avrebbe senso: la granularità è mensile):
   chiama `getMisurazioni` per `mese_da_importare`.
3. **Vuoto** → non fa nulla, resta fermo su quel mese, riprova al
   prossimo ciclo. **Nessun abbandono automatico** (a differenza di
   `ABBANDONO_CODA_DOPO_GIORNI` dei PCF): se un mese non viene mai
   pubblicato, il cursore resta bloccato lì finché non arriva - scelta
   deliberata, non un dimenticato "da fare". Chi vuole sbloccarsi a mano
   nel frattempo ha comunque il servizio condiviso `recupera_storico`
   (funziona indipendentemente dallo stato del cursore).
4. **Disponibile** → importa (statistics per `elementiCurve`), e avanza
   `mese_da_importare` al mese successivo. Torna al punto 2 - quando il
   cursore raggiunge il mese in corso, lo riprova come qualunque altro
   mese, senza bisogno di un caso speciale "mese in corso" vs "chiuso".
5. **Primo avvio**: nessun backfill storico. Il cursore parte dal mese
   in cui l'integrazione viene configurata (come i PCF: "al primo avvio
   non si richiedono dati storici").

### `recupera_storico` abilitato anche per Areti

Il servizio condiviso va esteso a `AretiCoordinator`, sul modello di
`EdistribuzioneCoordinator` (non dei PCF: Areti ha bisogno del `pod`
specifico per risalire a `codiceBP`/`codiceFiscale`, un account PCF
invece copre più POD nello stesso export):

```python
async def async_recupera_storico(
    self, data_da: date, data_a: date, pod: str | None = None
) -> None:
```

Stessa firma di `EdistribuzioneCoordinator.async_recupera_storico`: se
`pod` è omesso, lo fa per tutti i POD configurati sulla entry: se
specificato, solo per quello (`ServiceValidationError` se non è tra
quelli configurati).

**Differenza da segnalare all'utente** (in `strings.json`/
`translations/it.json`, e nella descrizione del campo in
`services.yaml`): Areti non ha una vera API a intervallo di date come
PCF/E-Distribuzione - `data_da`/`data_a` restano l'interfaccia del
servizio (coerente con gli altri distributori, stesso form nella UI),
ma internamente vengono convertite nell'insieme di `meseAnno` che
l'intervallo attraversa (`{f"{d.month:02d}{d.year}" for d in mesi tra
data_da e data_a}`), e per ciascuno si chiama `getMisurazioni` una
volta: il risultato è **il mese intero**, non i soli giorni richiesti
(l'API non offre altro). Chiedere un solo giorno in mezzo al mese
importa comunque tutto il mese che lo contiene.

File da toccare in `__init__.py` (oltre al nuovo pacchetto
`distributors/areti/`):
- `_recupera_storico`: aggiungere `AretiCoordinator` alla tupla
  `isinstance` accettata, e un branch che passa `pod=pod` (stesso ramo
  di `EdistribuzioneCoordinator`, non quello dei PCF che ignora `pod`).
- `_risolvi_coordinator_e_pod_da_device`: l'`isinstance(coordinator,
  EdistribuzioneCoordinator)` che oggi risolve il `pod` dal device
  selezionato va estesa a includere anche `AretiCoordinator` (stesso
  formato di identifiers `(DOMAIN, f"{entry_id}_{pod}")`, assumendo che
  i dispositivi Areti siano per-POD come quelli E-Distribuzione, non un
  device "account" unico come nei PCF).

## Cosa resta aperto (non blocca l'implementazione)

1. **Comportamento a sessione scaduta** — cosa risponde `/sfsites/aura`
   quando `sid`/`aura.token` non sono più validi (redirect? errore
   strutturato?), per capire come intercettarlo e rifare login. Da
   dedurre in fase di scrittura di `auth.py`/verificare in seguito.
2. **`fwuid` nel tempo** — è l'id di build del framework Aura, cambia a
   ogni release Salesforce (qualche volta l'anno): va riletto dall'HTML
   della pagina `/s/` a ogni sessione, non hardcodato.
3. **P.IVA / codice operatore ARERA** — ancora da confermare via
   risposta ARERA per un comune servito da Areti, per popolare
   `DISTRIBUTOR_REGISTRY`.
4. **Credenziali errate / OTP occasionale** — non osservati in questa
   cattura (un solo login riuscito, da dispositivo già noto). Se in
   produzione compare un passaggio diverso, va gestito quando si
   presenta.
5. Verificare se `getMisurazioni` supporta anche `UA` in modo utile per
   chi ha un impianto di produzione (fuori dallo scope minimo, ma utile
   saperlo per non doverci tornare).

Il modulo è scrivibile: pacchetto `distributors/areti/` (caso B di
`CONTRIBUTING.md`), con `auth.py` per il login a form + gestione
cookie/`aura.token`/`fwuid`, `api.py` per la catena di chiamate sopra,
`coordinator.py`/`statistics.py` sul modello di `edistribuzione/` per
l'import della curva a 15 minuti nella Energy Dashboard.

## Come contribuire

Il modulo è implementato e configurabile dalla UI. Il modo più utile di
aiutare ora è **usarlo su un account reale** e segnalare cosa non torna
— in particolare i punti ancora aperti sopra (comportamento a sessione
scaduta, credenziali errate, disponibilità infra-mensile). Per debug
rapido senza passare dalla UI di Home Assistant c'è ancora
[`scripts/verify_areti_login.py`](../scripts/verify_areti_login.py) (vedi
[`scripts.md`](scripts.md)), utile anche per verificare che login/curva
continuino a funzionare dopo un cambiamento lato Areti/Salesforce.

> [!CAUTION]
> Se produci una nuova cattura HAR per qualunque verifica: una HAR
> grezza **non è anonimizzata** — contiene la password in chiaro (il
> login è un POST su HTTPS, il valore finisce nel body catturato),
> cookie di sessione, `aura.token`, codice fiscale, POD, nome e
> indirizzo del titolare, e i valori di consumo reali.
> **Non va mai allegata a una issue pubblica.** Condividila in privato —
> apri prima una issue su
> <https://github.com/riccardorossi92/HomeAssistant-Contatore/issues>
> per concordare come.
