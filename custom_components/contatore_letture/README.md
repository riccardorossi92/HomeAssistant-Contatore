# contatore_letture — stato del pacchetto

## Architettura

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

## Script disponibili

Nessuno dei due era ancora documentato qui, nonostante siano entrambi
attivamente in uso - trovato durante un audit del repository il
21/08/2026.

- **`scripts/update_istat_snapshot.py`**: rigenera
  `data/istat_comuni_snapshot.json` (fallback usato quando il fetch live
  dei comuni ISTAT non è disponibile) dalla stessa sorgente usata a
  runtime. Lanciato in automatico dalla GitHub Action
  `.github/workflows/update-istat-snapshot.yml` il primo di ogni mese
  (o a mano da "Actions" su GitHub): apre una PR solo se lo snapshot è
  davvero cambiato.
- **`scripts/verify_edistribuzione_login.py`**: testa da terminale il
  login E-Distribuzione reale (email/password → OTP → recupero POD →
  curva di carico), senza passare da Home Assistant - molto più veloce
  per iterare durante il debug che riconfigurare l'integrazione ad ogni
  tentativo. Dopo un login riuscito salva il refresh_token su file
  locale (`edistribuzione_refresh_token.txt`, escluso da git): al lancio
  successivo permette di testare solo il refresh, senza rifare
  email/password/OTP - utile per verificare periodicamente se il
  refresh continua a funzionare nel tempo. Non è un test automatico
  (richiede credenziali reali digitate a mano): per quello vedi
  `tests/edistribuzione/`.

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

## Stato E-Distribuzione

Confermato funzionante con dati reali (non solo compilazione/sintassi):
login OAuth2+PKCE+OTP via Salesforce, recupero POD, import Energy
Dashboard (`statistics.py`, schema JSON confermato su una risposta reale
il 20/08/2026), multi-POD sulla stessa config entry, `recupera_storico`
con una singola chiamata a intervallo (confermato fino a 181 giorni),
reauth e options flow (aggiungi/rimuovi POD, orario) - stessa parità
funzionale di Duereti/Unareti.

**Confermato con dati reali** (21/08/2026): `val` nella curva di carico è
energia in kWh per intervallo di 15 minuti, non potenza media in kW.
Verificato confrontando il totale della curva per un mese intero (giugno
2026) con il delta di due letture ufficiali consecutive
(`async_get_reading`): 134.925 kWh in entrambi i casi, combacianti fino
alla terza cifra decimale - non una stima, un confronto diretto. Vedi il
docstring di `distributors/edistribuzione/statistics.py` per il dettaglio.

**Ancora aperto**:
- `RITARDO_DATI_GIORNI` (quando E-Distribuzione pubblica i dati del
  giorno prima) non è stato verificato empiricamente, a differenza di
  Duereti/Unareti - vedi la nota in
  `distributors/edistribuzione/const.py`. Dati reali raccolti il
  21/08/2026, entrambi verso l'01:00: il giorno immediatamente precedente
  (1 giorno prima) risulta non ancora disponibile (404), mentre quello di
  2 giorni prima è già disponibile. Restringe la finestra ma non la
  chiude: non dice ancora se il giorno precedente diventi disponibile più
  avanti nello stesso giorno (es. all'orario di richiesta di default,
  le 19:00) o solo il giorno dopo ancora.
- Refresh del `refresh_token` E-Distribuzione: un primo test a poche ore
  di distanza (21/08/2026, via `scripts/verify_edistribuzione_login.py`)
  ha confermato che funziona e che il token **non ruota** ad ogni uso
  (resta lo stesso). Non ancora confermato su un periodo più lungo
  (settimane/mesi) - da riverificare periodicamente con lo stesso
  script prima di considerarlo definitivamente affidabile.
- I regex di scraping in `auth.py` restano best-effort: se Enel cambia
  qualcosa lato loro, è probabile che si rompano di nuovo (è già successo
  più volte durante lo sviluppo - vedi la cronologia dei fix più sotto per
  farsi un'idea di cosa aspettarsi in quel caso).

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

1. Aggiorna `codeowners`/`documentation`/`issue_tracker` in `manifest.json`
   e `GITHUB_REPO_URL` in `const.py` con i tuoi riferimenti reali.
2. Se vuoi disinstallare `duereti_letture`/`unareti_letture` esistenti prima
   di installare `contatore_letture`, ricordati che è una rottura intenzionale
   (vedi sopra): non c'è continuità automatica delle statistiche.
3. Consigliato: un primo giro di test manuale del wizard end-to-end (region
   → provincia → comune → lookup ARERA → credenziali → POD) prima di fare
   affidamento sui servizi `recupera_ticket`/`recupera_storico`.
