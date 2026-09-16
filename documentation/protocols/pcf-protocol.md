# Duereti / Unareti — protocollo PCF

Codice: `custom_components/contatore_letture/distributors/pcf_common/`.
Protocollo "Portale Clienti Finali" (Client ID + Secret ID, abilitazione
manuale via portale), a due fasi: richiesta di un ticket di export
(`requestExport`), poi polling finché il ticket è pronto
(`requestResult`) e download del file (CSV per la curva, XLSX per le
letture periodiche).

## Modello dati: mese solare chiuso (dal 08/09/2026)

I manuali `Manuale_PCF_Rich_API` di Unareti e Duereti (identici tra loro a
parte l'`HOST_NAME`), rivisti l'8 settembre 2026, dichiarano per le CURVE:

> Le CURVE … sono disponibili solo i dati degli ultimi 5 anni **fino al
> mese appena concluso**; … non è possibile ricevere CURVE per date …
> **relative al mese corrente**. … non è possibile richiedere dati prima
> della data di installazione del contatore 2G.

Prima si poteva chiedere il singolo giorno (`dataDa == dataA` a `oggi-1`);
ora il mese corrente non è più disponibile per le curve. `requestExport`
accetta comunque un intervallo `dataDa`/`dataA` arbitrario (max 6 mesi),
purché non entri nel mese corrente. Le LETTURE resterebbero disponibili
fino a ieri, ma l'integrazione importa solo `MODE_CURVE`.

Di conseguenza `pcf_common` usa lo **stesso modello di Areti**: un
**cursore mensile persistito per POD** su `entry.data`
(`CONF_MESE_DA_IMPORTARE = {pod: "YYYY-MM"}` = prossimo mese da importare).
Ogni ciclo (una volta al giorno):

- si prende il più vecchio tra i cursori che puntano a un **mese già
  chiuso** e si fa **una** `requestExport` per il gruppo di POD che
  condividono quel mese (c'è un solo slot `CONF_PENDING_TICKET`, e ogni
  `requestResult` può restare in coda per ore: N export separati
  significherebbero attese seriali);
- quando il file arriva, ogni POD del gruppo per cui contiene dati avanza
  il cursore al mese successivo; gli altri restano fermi e vengono
  riprovati. **Nessun abbandono automatico** (scelta deliberata, come
  Areti): un mese mai pubblicato blocca il cursore di quel POD finché non
  arriva. L'escape è `contatore_letture.recupera_storico`;
- primo avvio (o POD aggiunto dalle opzioni): il cursore parte dal mese
  corrente, nessun backfill automatico;
- se le external statistics coprono già il mese del cursore (cursore
  perso, import fatto a mano) il ciclo avanza il cursore senza rifare la
  richiesta.

Non c'è più un "orario della richiesta" configurabile per Duereti/Unareti
(la voce resta solo per E-Distribuzione, che pubblica ancora giorno per
giorno). Gli helper di mese puri stanno in `pcf_common/date_utils.py`
(nessun import da `homeassistant`, così `api.py` li può usare per la
verifica del POD in configurazione).

`pcf_common/*` è condiviso tra Duereti e Unareti.
`distributors/duereti.py` e `distributors/unareti.py` sono comunque
volutamente **file separati** (non un'unica classe parametrizzata): se
domani uno dei due diverge davvero (endpoint diverso, comportamento
diverso), l'override va solo nel suo file, non serve toccare una classe
condivisa per entrambi.

> [!NOTE]
> **`recupera_storico`** fa un'unica richiesta `requestExport` per
> l'intero periodo (non un ciclo giorno per giorno), e vale sempre per
> l'intera configurazione insieme (tutti i POD, non un singolo POD come
> invece è possibile per E-Distribuzione). Il limite di 6 mesi per
> richiesta è qui documentato dalle API del distributore, non solo una
> cautela auto-imposta.

## Cosa è stato portato dal codice reale (non stub)

`pcf_common/*` è stato generato **a partire dal codice reale di
`duereti_letture` v0.7.2** (non riscritto a mano), con trasformazioni
scriptate per minimizzare il rischio di errori di trascrizione:

- rinominate le classi (`Duereti*` → `Pcf*`);
- parametrizzato `base_url` nel client API (prima hardcoded su Duereti);
- parametrizzato `display_name` in coordinator/sensor/statistics per i
  messaggi utente-facing e i nomi delle entità;
- **nessuna modifica alla logica** al momento del porting: retry del WAF,
  gestione dei 409/429/404, coda dei giorni da riprovare, calcolo del mese
  precedente completo, gestione del cambio ora legale — tutto identico
  all'originale.

> [!NOTE]
> Il `coordinator.py` è poi **divergito dall'originale** con il passaggio
> al modello a mese chiuso (08/09/2026, vedi sopra): la coda dei giorni da
> riprovare e l'orario configurabile sono stati rimossi, sostituiti dal
> cursore mensile per POD. `api.py` (parsing, WAF, gestione HTTP) è invece
> rimasto quello portato.

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

## Nota sulla documentazione del WAF/DST

Alcuni commenti tecnici in `pcf_common/api.py` e
`pcf_common/statistics.py` documentano comportamenti (blocchi del WAF,
interpretazione del flag ora legale nel CSV) **verificati sul campo solo
su Duereti**, non ancora riconfermati su Unareti. Sono segnalati
esplicitamente nei commenti: se in futuro emergono differenze su
Unareti, vanno annotate lì (non serve più toccare due file diversi con
la stessa logica duplicata, come accadeva prima dell'unificazione in
`pcf_common`).

In particolare:
- **Retry del WAF**: alcune risposte 403/429 sono blocchi temporanei del
  WAF del portale, non errori reali — gestiti con retry automatico.
- **Interpretazione del cambio ora legale**: il CSV della curva di
  carico Duereti porta un flag (`FL_ORA_LEGALE`) che va interpretato per
  calcolare correttamente il timestamp di ogni campione nei giorni di
  transizione — verificato su un export di 181 giorni comprendente una
  transizione reale. A differenza di E-Distribuzione (vedi
  `edistribuzione-protocol.md`), qui il timestamp non è già assoluto nel
  dato grezzo, va ricostruito interpretando il flag.

## Rottura intenzionale rispetto a duereti_letture/unareti_letture

Per scelta esplicita (nessun utente reale con storico da preservare oggi),
`contatore_letture` usa un **dominio HA unico** (`contatore_letture`)
invece dei due domini separati `duereti_letture`/`unareti_letture`. Lo
`statistic_id` generato (`contatore_letture:<pod>_energia`) è quindi
diverso da quello delle vecchie integrazioni: se in futuro ci saranno
utenti reali da migrare, servirà scrivere una migrazione esplicita che
rinomina gli `statistic_id` nel recorder prima del passaggio — non ancora
scritta, perché fuori scopo per ora.

Se vuoi disinstallare `duereti_letture`/`unareti_letture` esistenti prima
di installare `contatore_letture`, ricordati di questa rottura
intenzionale: non c'è continuità automatica delle statistiche.
