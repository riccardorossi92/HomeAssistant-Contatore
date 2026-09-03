"""Costanti condivise del protocollo PCF (Duereti/Unareti).

BASE_URL e DOMAIN NON stanno qui: BASE_URL è specifico di ogni distributore
(vedi distributors/duereti.py, distributors/unareti.py), DOMAIN è unificato
a livello di contatore_letture (vedi const.py principale).
"""

CONF_CLIENT_ID = "client_id"
CONF_SECRET_ID = "secret_id"
CONF_PODS = "pods"  # lista di dict: {"pod": "IT001...", "df": "RSSMRA..."} - df = dato fiscale (CF o P.IVA)

# Data (ISO) del giorno in cui l'integrazione è stata configurata. Al primo
# avvio non si richiedono dati: ci si limita a validare le credenziali, e le
# richieste giornaliere partono dal giorno successivo.
CONF_DATA_INSTALLAZIONE = "data_installazione"

# I dati di un giorno risultano disponibili il giorno successivo, ma non da
# subito: verificato sul campo che alle 10 del mattino il giorno precedente
# spesso non c'è ancora, mentre in serata sì. (Osservato su Duereti; da
# riconfermare su Unareti se emergono differenze.)
#
# ECCEZIONE NOTA - primi giorni del mese: il 02/09/2026 una requestExport
# per il 01/09 è stata rifiutata con HTTP 400 "errore nelle date inserite",
# mentre il 31/08 veniva accettata (verificato con una chiamata diretta,
# stesso POD e stesse credenziali). Il distributore sembra non pubblicare i
# giorni del mese corrente finché non ha chiuso il precedente.
# NON si alza questa costante a 2 per gestirlo: perderemmo un giorno di
# freschezza tutto l'anno per un problema che dura pochi giorni al mese, e
# il resto del tempo il giorno precedente è regolarmente disponibile in
# serata. Ci pensa la coda dei giorni da riprovare, che li riprende appena
# il distributore inizia ad accettarli.
RITARDO_DATI_GIORNI = 1

# Giorni per cui il distributore non ha (ancora) restituito dati, in attesa
# di essere richiesti di nuovo. Salvati sulla config entry come lista di date
# ISO: i dati possono arrivare con qualche giorno di ritardo, e senza una
# coda un giorno mancato resterebbe un buco permanente nello storico.
CONF_GIORNI_DA_RIPROVARE = "giorni_da_riprovare"

# Oltre questo numero di tentativi un giorno viene abbandonato: se dopo tanti
# giorni il distributore non lo ha prodotto, con ogni probabilità non lo farà
# mai (fornitura non attiva in quella data, o dato mai validato).
#
# Il valore va letto insieme al ritmo dei tentativi, non come "numero di
# giorni": il coordinator gira ogni ora ma chiede dati solo dopo
# ORA_MINIMA_RICHIESTA, quindi restano ~5 cicli utili a sera (19-23) e ogni
# ciclo incrementa il contatore di TUTTI i giorni in coda insieme (da quando
# il ciclo giornaliero chiede un intervallo, vedi _prossima_richiesta, non
# più un giorno alla volta).
#
# 30 tentativi ≈ 6 giorni di margine. Era 10 (≈2 giorni), troppo poco: a
# inizio mese il distributore può metterci diversi giorni a pubblicare i
# giorni del mese nuovo (osservato il 02/09/2026, vedi la nota su
# RITARDO_DATI_GIORNI sopra), e quei giorni venivano abbandonati prima di
# diventare disponibili, lasciando un buco permanente. Alzarlo non costa
# richieste in più: i tentativi viaggiano dentro la stessa chiamata che
# copre l'intero intervallo.
MAX_TENTATIVI_PER_GIORNO = 30

# Quanti giorni tenere in coda al massimo. Evita che un problema prolungato
# faccia crescere la coda senza limite; i giorni più vecchi vengono lasciati
# cadere per primi.
#
# Nel ciclo automatico questo limite non viene mai avvicinato: la coda si
# stabilizza intorno ai giorni di margine concessi da
# MAX_TENTATIVI_PER_GIORNO (~6). Serve invece quando un import copre un
# periodo lungo e il file torna incompleto: lì vengono accodati molti giorni
# in un colpo solo.
MAX_GIORNI_IN_CODA = 30

# Attesa suggerita all'utente quando il blocco riguarda l'autenticazione:
# in quel caso il problema è generalizzato e non serve riprovare subito.
MINUTI_ATTESA_SUGGERITI = 10

# Giorni indietro usati dalla verifica del POD in fase di configurazione.
# Uno in più del ritardo normale: la verifica può avvenire a qualunque ora,
# anche di notte, quando i dati del giorno precedente potrebbero non essere
# ancora pronti. Chiedendo un giorno più vecchio il ticket che ne risulta
# punta a dati quasi certamente disponibili, quindi è subito utile invece di
# restare a lungo in coda.
RITARDO_VERIFICA_POD_GIORNI = RITARDO_DATI_GIORNI + 1

# Fasi con cui viene marcato un ticket in sospeso, per sapere come trattarlo
# quando viene ripreso. Stanno qui e non nel coordinator perché servono anche
# al config flow, che altrimenti creerebbe un'importazione circolare.
FASE_GIORNALIERO = "giornaliero"
FASE_STORICO = "storico"
FASE_MANUALE = "manuale"

# Prima di quest'ora (locale) non si chiede nulla. Alle 10 i dati del giorno
# precedente risultavano spesso non ancora pronti; alle 19 sì (verificato più
# volte, su Duereti). L'orario esatto di pubblicazione non è documentato e
# può variare, quindi è modificabile dalle opzioni dell'integrazione: questo
# è solo il valore di partenza. In ogni caso un giorno non restituito finisce
# comunque nella coda dei giorni da riprovare, quindi non va perso.
ORA_MINIMA_RICHIESTA = 19

# Chiave con cui l'orario scelto dall'utente è salvato nelle opzioni.
CONF_ORA_RICHIESTA = "ora_richiesta"

# Ticket requestExport in sospeso, salvato sulla config entry (sopravvive ai
# reload/riavvii) così un reload non perde di vista un ticket già ottenuto
# dal distributore - altrimenti si rischia di rifare requestExport da capo
# mentre quello precedente sta ancora venendo processato lato loro.
CONF_PENDING_TICKET = "pending_ticket"
CONF_PENDING_DATA_DA = "pending_data_da"
CONF_PENDING_DATA_A = "pending_data_a"
CONF_PENDING_IS_BACKFILL = "pending_is_backfill"

# Il token dichiarato dal manuale è valido 10 minuti. Un margine troppo ampio
# fa scattare il 409 CONFLICT (richiesta di nuovo token mentre il precedente
# è ancora attivo lato server), quindi lo teniamo piccolo.
TOKEN_VALIDITY_SECONDS = 600
TOKEN_SAFETY_MARGIN_SECONDS = 15

MODE_CURVE = "CURVE"
# Non usata dall'integrazione (che importa solo le curve): resta come
# riferimento al protocollo, ed è la modalità gestita da parse_letture_zip.
MODE_LETTURE = "LETTURE"

# Polling di requestResult: il job è schedulato lato distributore e può
# richiedere ore. Intervalli troppo ravvicinati non hanno senso e sprecano
# chiamate; usiamo 30 minuti tra un tentativo e l'altro. Essendo più lungo
# dei 10 minuti di validità del token, ad ogni tentativo il token va
# comunque rinnovato (gestito automaticamente da _get_token in api.py).
RESULT_POLL_INTERVAL_SECONDS = 1800  # 30 minuti
RESULT_POLL_MAX_ATTEMPTS = 12  # ~6 ore totali di attesa massima

# I dati vengono probabilmente validati/chiusi a fine mese, non giorno per
# giorno: il coordinator richiede sempre l'intero mese precedente completo
# (vedi coordinator._mese_precedente_completo) e si auto-limita a non
# rifare la stessa richiesta più volte per lo stesso mese. Un controllo
# giornaliero va bene: se il mese è già stato importato, il coordinator
# non rifà comunque la chiamata.
DEFAULT_SCAN_INTERVAL_HOURS = 1

ESITO_OK = 0
# Non usata: il codice verifica esito != ESITO_OK. Documentata per
# completezza, il manuale definisce solo questi due valori.
ESITO_ERRORE = 1

# Limiti dichiarati dal manuale (sezione requestExport). Non imposti dal
# codice: l'integrazione fa una richiesta per volta, quindi il limite di
# concorrenza non è raggiungibile. Documentato per chi legge.
MAX_CONCURRENT_REQUESTS = 5
MAX_SUPPLY_POINTS_PER_REQUEST = 200
MAX_DATE_RANGE_MONTHS = 6
