# Architettura

```
contatore_letture/
  config_flow.py         # orchestratore: wizard ARERA + step "pcf"/"edistribuzione" + reauth + options
  __init__.py             # setup/unload, dispatch per distributore, servizi condivisi
  sensor.py                # dispatch verso il modulo del distributore
  istat_comuni.py           # elenco comuni: fetch live + fallback snapshot
  istat_transform.py         # trasformazione pura lista->albero (usata da istat_comuni.py e scripts/)
  arera_lookup.py              # query live ARERA (comune -> distributore)
  distributors/
    __init__.py               # registry: DISTRIBUTOR_REGISTRY, PIVA_TO_KEY
    duereti.py                 # BASE_URL/DISPLAY_NAME/PIVA Duereti (sottile)
    unareti.py                  # BASE_URL/DISPLAY_NAME/PIVA Unareti (sottile)
    pcf_common/                   # libreria condivisa Duereti/Unareti
      api.py                       # client requestToken/requestExport/requestResult
      coordinator.py                 # polling, coda giorni da riprovare, retry ticket
      statistics.py                   # import external statistics + gestione DST
      sensor.py                        # entità diagnostiche (parametrizzate su display_name)
      config_flow_helpers.py            # validazione credenziali/POD condivisa
      const.py                            # costanti di protocollo condivise
    edistribuzione/                # pacchetto a sé, protocollo OAuth2+PKCE+OTP
      auth.py                       # login Salesforce (email/password -> OTP -> token)
      api.py                         # client REST (getSupplies, reading, curve di carico)
      coordinator.py                  # polling multi-POD, coda per POD, recupera_storico
      sensor.py                        # entità diagnostiche per POD (ultima data disponibile, consumo giorno)
      statistics.py                     # import external statistics (curva di carico)
      const.py                           # costanti di protocollo + schedulazione
```

`distributors/duereti.py` e `distributors/unareti.py` sono volutamente
**file separati** (non un'unica classe parametrizzata): se domani uno dei
due diverge davvero (endpoint diverso, comportamento diverso), l'override
va solo nel suo file, non serve toccare una classe condivisa per entrambi.

## Cosa è stato portato dal codice reale (non stub)

`pcf_common/*` è stato generato **a partire dal codice reale di
`duereti_letture` v0.7.2** (non riscritto a mano), con trasformazioni
scriptate per minimizzare il rischio di errori di trascrizione:

- rinominate le classi (`Duereti*` → `Pcf*`);
- parametrizzato `base_url` nel client API (prima hardcoded su Duereti);
- parametrizzato `display_name` in coordinator/sensor/statistics per i
  messaggi utente-facing e i nomi delle entità;
- **nessuna modifica alla logica**: retry del WAF, gestione dei
  409/429/404, coda dei giorni da riprovare, calcolo del mese precedente
  completo, gestione del cambio ora legale — tutto identico all'originale.

Verificato dopo la trasformazione:
- sintassi Python valida su tutti i file (`py_compile`);
- tutti gli import relativi risolvono ai nomi realmente definiti
  (controllo scriptato dedicato, non solo `py_compile`);
- `pyflakes` pulito — i soli avvisi residui (due riferimenti a tipo
  posticipato `"datetime"` in `pcf_common/api.py`, lasciati con un import
  differito per un motivo esplicito di "evitare cicli" già commentato nel
  file) sono stati rivisti in un audit del repository (21/08/2026): tutto
  il resto (variabile d'eccezione non riletta, type hint testuali altrove)
  è stato ripulito.

## Rottura intenzionale rispetto a duereti_letture/unareti_letture

Per scelta esplicita (nessun utente reale con storico da preservare oggi),
`contatore_letture` usa un **dominio HA unico** (`contatore_letture`)
invece dei due domini separati `duereti_letture`/`unareti_letture`. Lo
`statistic_id` generato (`contatore_letture:<pod>_energia`) è quindi
diverso da quello delle vecchie integrazioni: se in futuro ci saranno
utenti reali da migrare, servirà scrivere una migrazione esplicita che
rinomina gli `statistic_id` nel recorder prima del passaggio — non ancora
scritta, perché fuori scopo per ora.

## Prima di usarlo su un'installazione reale

1. Se vuoi disinstallare `duereti_letture`/`unareti_letture` esistenti
   prima di installare `contatore_letture`, ricordati che è una rottura
   intenzionale (vedi sopra): non c'è continuità automatica delle
   statistiche.
2. Consigliato: un primo giro di test manuale del wizard end-to-end
   (regione → provincia → comune → lookup ARERA → credenziali → POD)
   prima di fare affidamento sui servizi `recupera_ticket`/`recupera_storico`.
