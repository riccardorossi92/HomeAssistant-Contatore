"""Costanti condivise del protocollo PCF (Duereti/Unareti).

BASE_URL e DOMAIN NON stanno qui: BASE_URL è specifico di ogni distributore
(vedi distributors/duereti.py, distributors/unareti.py), DOMAIN è unificato
a livello di contatore_letture (vedi const.py principale).
"""

CONF_CLIENT_ID = "client_id"
CONF_SECRET_ID = "secret_id"
CONF_PODS = "pods"  # lista di dict: {"pod": "IT001...", "df": "RSSMRA..."} - df = dato fiscale (CF o P.IVA)

# Cursore mensile persistito PER POD, su entry.data: {pod: "YYYY-MM"} = il
# prossimo mese solare da importare per quel POD. Dal 08/09/2026 i manuali
# Unareti/Duereti dichiarano che le CURVE sono disponibili "solo fino al
# mese appena concluso" e "non è possibile ricevere CURVE relative al mese
# corrente": il dato non è più giornaliero, si pubblica a mese chiuso (come
# Areti). Non essendoci un ritardo fisso da cui dedurre quale mese chiedere,
# il coordinator avanza un cursore per POD invece del vecchio schema
# "sempre il giorno/mese precedente rispetto a oggi" - vedi
# coordinator.PcfCoordinator.
CONF_MESE_DA_IMPORTARE = "mese_da_importare"

# Minuti suggeriti all'utente quando il blocco riguarda l'autenticazione:
# in quel caso il problema è generalizzato e non serve riprovare subito.
MINUTI_ATTESA_SUGGERITI = 10

# Fasi con cui viene marcato un ticket in sospeso, per sapere come trattarlo
# quando viene ripreso. Stanno qui e non nel coordinator perché servono anche
# al config flow, che altrimenti creerebbe un'importazione circolare.
#
# FASE_AUTOMATICA aveva valore "giornaliero" prima del passaggio al modello
# a mese chiuso: _fase_da_entry ricade comunque su FASE_AUTOMATICA per
# qualunque valore non riconosciuto, quindi i ticket pendenti salvati dalle
# versioni precedenti restano gestiti correttamente.
FASE_AUTOMATICA = "automatica"
FASE_STORICO = "storico"
FASE_MANUALE = "manuale"

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

# I dati si pubblicano a mese solare chiuso, non giorno per giorno: la
# granularità utile è mensile, quindi un controllo al giorno è sufficiente
# (come per Areti). Ogni ciclo, per ciascun POD, il coordinator guarda il
# suo cursore: se punta a un mese già chiuso lo chiede, altrimenti resta
# fermo. Il polling del file di un ticket già ottenuto è su un task in
# background a parte (RESULT_POLL_INTERVAL_SECONDS), indipendente da questo
# intervallo.
DEFAULT_SCAN_INTERVAL_HOURS = 24

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

# Le CURVE sono disponibili solo per "gli ultimi 5 anni fino al mese appena
# concluso" (manuale, sezione requestExport): recupera_storico rifiuta le
# date più vecchie di così invece di far restare un ticket in coda per un
# periodo che il distributore non ha.
MAX_ANNI_STORICO = 5
