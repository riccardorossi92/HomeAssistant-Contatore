# Come contribuire

Guida pratica per due scenari concreti: **aggiungere un nuovo
distributore** e **modificare uno già esistente**. Per capire come
funziona un protocollo specifico prima di toccarlo, vedi
[`documentation/`](documentation/) (architettura generale,
`pcf-protocol.md`, `edistribuzione-protocol.md`).

## Aiutare senza scrivere codice: raccolta dati

Il collo di bottiglia per aggiungere un distributore quasi mai è il
codice: è **avere accesso a una fornitura reale** su cui vedere come
rispondono le sue API. Se hai una fornitura con un distributore non
ancora supportato, puoi essere d'aiuto anche senza toccare Python.

Caso già pronto: **Ireti**, dove autenticazione e anagrafica sono già
state analizzate e mancano solo gli endpoint dei consumi. C'è uno script
che raccoglie il necessario in un report anonimizzato — vedi
[`documentation/ireti-protocol.md`](documentation/ireti-protocol.md).

Per un distributore ancora del tutto inesplorato, la strada è una
**cattura HAR** del portale (login + pagina dei consumi con un grafico
caricato). Attenzione: una HAR grezza contiene token di sessione e dati
personali in chiaro, quindi **non va allegata a una issue pubblica** —
apri prima una issue per concordare come condividerla.

Non tutti i distributori sono fattibili: **Areti** (Roma) è stato
analizzato e scartato — il suo portale non espone i consumi. La ricerca
è in [`documentation/areti-protocol.md`](documentation/areti-protocol.md),
utile per non ripartire da zero.

## Prima di tutto: che tipo di distributore stai aggiungendo?

Ci sono due scenari molto diversi per costo/complessità.

### A. Stesso protocollo di uno già esistente (es. un altro distributore PCF)

Se il nuovo distributore usa lo **stesso protocollo "Portale Clienti
Finali"** di Duereti/Unareti (Client ID + Secret ID, export a ticket) —
è il caso facile: **non serve toccare `config_flow.py` né
`pcf_common/`**, basta un nuovo modulo sottile, sullo stesso modello di
`distributors/duereti.py`.

**File da creare:**
- `custom_components/contatore_letture/distributors/<nome>.py` — copia
  `duereti.py` o `unareti.py` come modello: `DISPLAY_NAME`, `PIVA`
  (verificata via HAR o scheda operatore ARERA — non inventarla),
  `BASE_URL`, `PORTAL_URL`, `REQUIRED_INFO`, più le quattro funzioni
  sottili (`create_coordinator`, `async_valida_credenziali`,
  `async_valida_pod`, `build_sensor_entities`) che delegano tutte a
  `pcf_common`.

**File da modificare:**
- `distributors/__init__.py` — importa il nuovo modulo e aggiungi una
  entry a `DISTRIBUTOR_REGISTRY` con `"kind": "pcf"` (copia la forma
  delle entry `duereti`/`unareti` esistenti).

**Cosa NON serve toccare**: `config_flow.py` (i suoi step "pcf" sono già
generici, parametrizzati sul `DISTRIBUTOR_REGISTRY`), `__init__.py`
(stesso discorso), `strings.json`/`translations/it.json` (gli step "pcf"
riusano le stesse chiavi), `sensor.py` dispatcher, i servizi
`recupera_storico`/`recupera_ticket` (funzionano già per qualunque
`PcfCoordinator`).

**Test**: aggiungi il nuovo modulo a
`tests/pcf_common/test_api.py`/`test_api_errori.py` solo se introduce un
comportamento diverso da Duereti/Unareti (endpoint diverso, quirk
specifico) — altrimenti la copertura esistente su `pcf_common/` vale
già anche per lui, dato che la logica è condivisa.

### B. Protocollo completamente nuovo (come E-Distribuzione)

Se il nuovo distributore ha un meccanismo di autenticazione/API
strutturalmente diverso (non Client ID/Secret ID + export a ticket) — è
il caso complesso, richiede un pacchetto a sé stante e modifiche in più
punti dell'orchestratore condiviso.

**File da creare** (nuovo pacchetto
`distributors/<nome>/`, sullo stesso modello di `distributors/edistribuzione/`):
- `const.py` — costanti di protocollo, endpoint, e se serve import
  automatico giornaliero: `CONF_DATA_INSTALLAZIONE`,
  `CONF_GIORNI_DA_RIPROVARE`, `CONF_ORA_RICHIESTA`,
  `RITARDO_DATI_GIORNI`, `ABBANDONO_CODA_DOPO_GIORNI`, `MAX_GIORNI_IN_CODA`,
  `ORA_MINIMA_RICHIESTA` (stessi nomi di `edistribuzione/const.py`, per
  coerenza — anche se duplicati invece che condivisi, per scelta
  esplicita: vedi `documentation/architecture.md`).
- `auth.py` (se il login richiede più di semplici credenziali statiche —
  es. OAuth, OTP, scraping di pagine).
- `api.py` — client HTTP per i dati veri e propri.
- `coordinator.py` — `DataUpdateCoordinator`, con `_async_update_data`,
  `async_recupera_storico(data_da, data_a, pod=None)` se vuoi che
  `recupera_storico` funzioni anche qui (firma compatibile con
  `EdistribuzioneCoordinator`/`PcfCoordinator`).
- `sensor.py` — `build_<nome>_entities(coordinator)`, entità diagnostiche
  minime (vedi nota sotto).
- `statistics.py` — `async_import_curva_giornaliera` (o equivalente) per
  scrivere nelle external statistics della Energy Dashboard.

**File da modificare:**
- `distributors/__init__.py` — nuova entry in `DISTRIBUTOR_REGISTRY` con
  un `"kind"` nuovo (non riusare `"edistribuzione"` per un protocollo
  diverso: quella stringa è usata per instradare a step di config flow
  specifici di Salesforce/OTP).
- `config_flow.py`:
  - `async_step_distributor_info`: oggi decide lo step successivo con
    `if kind == "pcf": ... else: edistribuzione_user` — **binario**, non
    un dispatch che scala a un terzo kind. Va cambiato in un
    `if`/`elif`/`elif` esplicito sui kind noti.
  - Nuovi step (`async_step_<nome>_...`) per il flusso di login
    specifico, sul modello di `async_step_edistribuzione_user`/`_otp`/`_pod`.
  - Se vuoi supportare il reauth: branch in `async_step_reauth` (oggi
    `if kind == "pcf": ... elif kind == "edistribuzione": ... else:
    abort`) + nuovi step `async_step_<nome>_reauth_...`.
  - Se vuoi un options flow (aggiungi/rimuovi POD, orario): branch in
    `ContatoreLettureOptionsFlow.async_step_init` + nuovi step.
- `__init__.py`:
  - `async_setup_entry`: nuovo branch `if info["kind"] == "<nome>": ...`
    (crea il coordinator, registra i servizi condivisi se pertinente,
    fa il primo refresh).
  - `async_unload_entry`: se il nuovo coordinator deve influenzare la
    rimozione dei servizi condivisi (`recupera_storico`), aggiorna i
    controlli `pcf_rimasti`/`edistribuzione_rimasti` per includere anche
    il nuovo tipo.
  - Se vuoi che `recupera_storico` funzioni anche per il nuovo
    distributore: aggiungi il suo import e includilo nella tupla di tipi
    accettati in `_recupera_storico`, e nel branch
    `isinstance(coordinator, EdistribuzioneCoordinator)` (da estendere o
    generalizzare se i distributori "con parametro pod opzionale"
    diventano più di uno).
- `strings.json` **e** `translations/it.json` (vanno tenuti identici,
  vedi sotto): nuove chiavi per ogni nuovo step di config flow/options
  flow, sotto `config.step`/`config.error`/`options.step`/`options.abort`
  a seconda di cosa aggiungi.
- `services.yaml`: solo se cambi la forma di un servizio condiviso (non
  serve toccarlo per aggiungere un distributore che riusa
  `recupera_storico` con la firma esistente).

**Entità minime consigliate** (vedi `edistribuzione/sensor.py` come
modello, non `pcf_common/sensor.py` a 5 sensori): un dispositivo
"Account" più uno per POD, con almeno "Ultima data disponibile" e
"Consumo ultimo giorno/periodo importato" — evita di esporre un sensore
per ogni combinazione fascia/grandezza se il dato equivalente vive già
nelle statistiche esterne (è stato un errore fatto e poi corretto per
E-Distribuzione, vedi la cronologia dei commit).

**Test**: nuova cartella `tests/<nome>/`, stesso schema di
`tests/edistribuzione/` (`__init__.py` vuoto — necessario per evitare
collisioni di nomi tra cartelle diverse con file `test_api.py` omonimi,
vedi commit dedicato in cronologia — più `test_api.py`/`test_auth.py`
con fixture basate su payload **reali**, non inventati, dove possibile).

## Modificare un distributore esistente

- **Cambi che riguardano solo Duereti O solo Unareti** (non entrambi):
  vanno nel rispettivo `distributors/duereti.py`/`unareti.py`, mai in
  `pcf_common/` — altrimenti l'altro distributore erediterebbe un
  comportamento pensato per uno solo dei due.
- **Cambi che riguardano il protocollo PCF in generale** (entrambi
  Duereti e Unareti): vanno in `pcf_common/`. Se il comportamento è
  stato verificato sul campo solo su uno dei due, dillo esplicitamente
  nel commento (vedi la nota WAF/DST in `documentation/pcf-protocol.md`
  come esempio di come è già stato fatto).
- **Cambi a E-Distribuzione**: quasi tutto vive in
  `distributors/edistribuzione/`. Se tocchi `auth.py` (login/OTP) o il
  parsing delle risposte, verifica con
  `scripts/verify_edistribuzione_login.py` prima di aprire una PR — è
  molto più veloce che passare dalla UI di Home Assistant, e l'unico modo
  per sapere se un cambiamento lato Enel ha rotto qualcosa (è già successo
  più volte, vedi `documentation/edistribuzione-protocol.md`).
- **`strings.json`/`translations/it.json`**: vanno sempre tenuti
  **identici** (l'italiano è al momento l'unica lingua supportata,
  duplicata in entrambi i file). Se aggiungi/cambi una chiave in uno,
  copiala nell'altro nello stesso commit.

## Checklist prima di aprire una PR

1. `python3 -m py_compile` su ogni file toccato.
2. `pyflakes` sui file toccati — zero avvisi nuovi rispetto a quelli già
   noti (vedi `documentation/architecture.md` per l'elenco degli avvisi
   accettati e perché).
3. `ruff check .` — pulito (config in `pyproject.toml`; è lo stesso
   controllo che gira in CI nel workflow *Lint*).
4. `pytest tests/pcf_common/test_api.py tests/pcf_common/test_api_errori.py tests/edistribuzione/`
   (i test che non richiedono Home Assistant installato) — devono
   passare tutti. La suite completa (`pytest`) gira in CI nel workflow
   *Test*.
5. Se hai toccato `services.yaml`: verificalo con
   `python3 -c "import yaml; yaml.safe_load(open('custom_components/contatore_letture/services.yaml'))"`
   **e** controlla che ogni campo abbia un solo `selector` (un YAML
   sintatticamente valido può comunque avere una struttura che Home
   Assistant rifiuta — è già successo, vedi cronologia dei commit).
6. Se hai toccato la logica di un distributore con dati reali a
   disposizione: preferisci verificarla con dati veri (script da
   terminale, o un confronto come quello fatto per confermare
   l'assunzione kWh di E-Distribuzione) piuttosto che assumere che il
   comportamento sia corretto solo perché il codice compila.
