# contatore_letture — stato del pacchetto

## Architettura

```
contatore_letture/
  config_flow.py             # orchestratore: wizard ARERA + step generici "pcf" + reauth + options
  __init__.py                 # setup/unload, dispatch per distributore, servizi condivisi
  sensor.py                    # dispatch verso il modulo del distributore
  istat_comuni.py               # elenco comuni: fetch live + fallback snapshot
  istat_transform.py             # trasformazione pura lista->albero (condivisa con scripts/)
  arera_lookup.py                 # query live ARERA (comune -> distributore)
  distributors/
    __init__.py                   # registry: DISTRIBUTOR_REGISTRY, PIVA_TO_KEY
    duereti.py                     # BASE_URL/DISPLAY_NAME/PIVA Duereti (sottile, usa pcf_common)
    unareti.py                      # BASE_URL/DISPLAY_NAME/PIVA Unareti (sottile, usa pcf_common)
    pcf_common/                      # libreria condivisa Duereti/Unareti
      api.py                          # client requestToken/requestExport/requestResult
      coordinator.py                    # polling, coda giorni da riprovare, retry ticket
      statistics.py                      # import external statistics + gestione DST
      sensor.py                           # entità diagnostiche (parametrizzate su display_name)
      config_flow_helpers.py               # validazione credenziali/POD condivisa
      const.py                              # costanti di protocollo condivise
    edistribuzione/                  # pacchetto a se', protocollo diverso (OAuth2+PKCE/OTP)
      auth.py                          # login Salesforce Aura + OTP + scambio token
      api.py                            # client REST verso il backend misure
      coordinator.py                     # refresh token + polling reading/time-of-use
      sensor.py                           # entità (lettura per fascia, picchi di potenza)
      statistics.py                        # STUB - vedi "Cosa resta STUB" sotto
      const.py

scripts/
  update_istat_snapshot.py    # rigenera data/istat_comuni_snapshot.json (usato dalla Action)

.github/workflows/
  update-istat-snapshot.yml   # cron mensile, apre una PR se lo snapshot ISTAT e' cambiato
```

## Cosa è stato portato dal codice reale (non stub)

`pcf_common/*` è stato generato **a partire dal codice reale di `duereti_letture` v0.7.2**
(non riscritto a mano), con trasformazioni scriptate per minimizzare il rischio di
errori di trascrizione:

- rinominate le classi (`Duereti*` → `Pcf*`);
- parametrizzato `base_url` nel client API (prima hardcoded su Duereti);
- parametrizzato `display_name` in coordinator/sensor/statistics per i messaggi
  utente-facing e i nomi delle entità;
- **nessuna modifica alla logica**: retry del WAF, gestione dei 409/429/404,
  coda dei giorni da riprovare, calcolo del mese precedente completo, gestione
  del cambio ora legale — tutto identico all'originale.

Verificato dopo la trasformazione:
- sintassi Python valida su tutti i file (`py_compile`);
- tutti gli import relativi risolvono ai nomi realmente definiti (controllo
  scriptato dedicato, non solo `py_compile`);
- `pyflakes` pulito — i soli avvisi residui (due riferimenti a tipo posticipato
  `"datetime"`/`"date"` in stringa, una variabile d'eccezione non riletta in un
  ramo che fa `raise` bare) erano già presenti identici nel codice originale
  Duereti, non introdotti dalla trasformazione.

`distributors/duereti.py` e `distributors/unareti.py` sono volutamente **file separati**
(non un'unica classe parametrizzata): se domani uno dei due diverge davvero
(endpoint diverso, comportamento diverso), l'override va solo nel suo file.

## Nota sulla documentazione del WAF/DST

Alcuni commenti tecnici in `pcf_common/api.py` e `pcf_common/statistics.py`
documentano comportamenti (blocchi del WAF, interpretazione del flag ora
legale) **verificati sul campo solo su Duereti**, non ancora riconfermati su
Unareti. Sono segnalati esplicitamente nei commenti: se in futuro emergono
differenze su Unareti, vanno annotate lì (non serve più toccare due file
diversi con la stessa logica duplicata, come accadeva prima).

## Rottura intenzionale rispetto a duereti_letture/unareti_letture

Per scelta esplicita (nessun utente reale con storico da preservare oggi),
`contatore_letture` usa un **dominio HA unico** (`contatore_letture`) invece
dei due domini separati `duereti_letture`/`unareti_letture`. Lo `statistic_id`
generato (`contatore_letture:<pod>_energia`) è quindi diverso da quello delle
vecchie integrazioni: se in futuro ci saranno utenti reali da migrare, servirà
scrivere una migrazione esplicita che rinomina gli `statistic_id` nel recorder
prima del passaggio — non ancora scritta, perché fuori scopo per ora.

## Cosa resta STUB / non ancora verificato

- **Login E-Distribuzione (`distributors/edistribuzione/auth.py`)**: NON
  ancora confermato funzionante end-to-end. Come previsto dal README della
  bozza originale ("i regex di scraping sono best-effort, non testati
  contro l'HTML reale"), il primo test reale (19-20/08/2026) ha trovato e
  corretto due bug concreti:
  - un loop di redirect infinito (`TooManyRedirects`) causato da una
    doppia codifica URL nel redirect automatico di aiohttp — risolto
    seguendo i redirect a mano con `encoded=True` (v0.0.4);
  - l'estrazione del `fwuid` cercava una forma letterale `"fwuid":"..."`
    nell'HTML, ma il valore reale è incorporato percent-encoded nel path
    di un URL di bootstrap (`<script src="/sfsites/l/%7B...%7D/app.js">`)
    — risolto aggiungendo quella forma come fallback (v0.0.5).

  **Ancora da verificare**: l'estrazione del token Aura
  (`_extract_aura_token`) non ha ancora superato un test reale — la
  pagina di login catturata finora non sembra contenerlo nella forma
  attesa dal regex originale. Il login potrebbe fallire al passo
  successivo finché questo non viene verificato/corretto allo stesso modo.
- **`distributors/edistribuzione/statistics.py`**: import nella Energy
  Dashboard NON ancora implementato. Il blocco mancante è lo schema JSON
  della curva di carico (`async_get_daily_load_profile`/
  `async_get_monthly_load_profile`): non documentato da nessuna parte,
  quindi `statistics.py` oggi si limita a loggare la struttura ricevuta
  invece di inventare nomi di campo.
- **Reauth/Options flow per E-Distribuzione**: abortiscono esplicitamente
  (`reauth_not_supported` / `options_not_supported`) invece di fallire in
  modo oscuro - il modello di autenticazione (OAuth2+OTP) è troppo diverso
  da quello "pcf" per riusare la stessa logica di reauth.

## Nota di sicurezza (E-Distribuzione)

Le HAR usate per costruire `distributors/edistribuzione/auth.py`
contenevano credenziali reali (password, access/refresh token, sid di
sessione) di un account di test. Le HAR sono state condivise solo in
questa chat (non pubblicate altrove) e non conservate oltre l'analisi; la
password dell'account di test è già stata cambiata. Nessuna azione
ulteriore necessaria — nota lasciata qui solo come promemoria per chi in
futuro dovesse catturare nuove HAR per aggiornare `auth.py`: fare lo
stesso, non committare mai le catture originali nel repository.

## Prima di usarlo su un'installazione reale

1. Se vuoi disinstallare `duereti_letture`/`unareti_letture` esistenti prima
   di installare `contatore_letture`, ricordati che è una rottura intenzionale
   (vedi sopra): non c'è continuità automatica delle statistiche.
2. Consigliato: un primo giro di test manuale del wizard end-to-end (region
   → provincia → comune → lookup ARERA → credenziali → POD) prima di fare
   affidamento sui servizi `recupera_ticket`/`recupera_storico`.
