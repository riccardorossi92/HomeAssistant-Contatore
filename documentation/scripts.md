# Script disponibili

Script in `scripts/`, tutti attivamente in uso.

## `scripts/update_istat_snapshot.py`

Rigenera `custom_components/contatore_letture/data/istat_comuni_snapshot.json`
(fallback usato quando il fetch live dei comuni ISTAT non è disponibile a
runtime) dalla stessa sorgente usata dall'integrazione. Lanciato in
automatico dalla GitHub Action `.github/workflows/update-istat-snapshot.yml`
il primo di ogni mese (o a mano da "Actions" su GitHub): apre una PR solo
se lo snapshot è davvero cambiato.

## `scripts/verify_edistribuzione_login.py`

Testa da terminale il login E-Distribuzione reale (email/password → OTP →
recupero POD → curva di carico), senza passare da Home Assistant — molto
più veloce per iterare durante il debug che riconfigurare l'integrazione
ad ogni tentativo. Importa direttamente `auth.py`/`api.py` (che non
dipendono da Home Assistant), bypassando i punti della gerarchia di
pacchetti che lo richiederebbero.

Funzionalità:
- **Login completo**: email/password → OTP → elenco POD → curva di
  carico per un intervallo di date a scelta (utile anche per testare se
  l'endpoint supporta davvero un range o lo tronca).
- **Test del solo refresh**: dopo un login riuscito, salva il
  `refresh_token` in `edistribuzione_refresh_token.txt` (file locale,
  escluso da git — non va mai committato). Al lancio successivo, offre di
  testare solo `async_refresh_access_token` con quel token salvato, senza
  rifare email/password/OTP — il modo pratico per verificare
  periodicamente se il refresh continua a funzionare nel tempo (cosa non
  testabile in un colpo solo come il login).
- **Confronto con la lettura ufficiale**: dopo aver recuperato la curva
  di carico, permette di confrontarne il totale con il delta di due
  letture ufficiali consecutive per lo stesso periodo (`async_get_reading`)
  — è così che è stata confermata l'assunzione "val = kWh per intervallo"
  in `edistribuzione/statistics.py` (vedi `edistribuzione-protocol.md`).

Non è un test automatico (richiede credenziali reali digitate a mano): per
quello vedi `tests/edistribuzione/`.

## `scripts/raccogli_dati_ireti.py`

Per chi ha una fornitura **Ireti** e vuole aiutare a implementarne il
supporto (non ancora disponibile). Fa login, legge l'anagrafica, estrae
gli endpoint dal bundle JavaScript dell'app e prova quelli di misura,
salvando un report **anonimizzato** (`ireti_report.json`) da allegare a
una issue. Non invia nulla: scrive solo un file locale.

Dettagli e stato della ricerca in
[`ireti-protocol.md`](ireti-protocol.md).

## `scripts/verify_areti_login.py`

Testa da terminale il login **Areti** (email/password → ticket-exchange
`frontdoor.jsp` → sessione Lightning) e il recupero della curva di
carico a 15 minuti per un mese scelto, implementando da zero il
protocollo documentato in [`areti-protocol.md`](areti-protocol.md) - non
importa `distributors/areti/` (a differenza di
`verify_edistribuzione_login.py`, che importa `auth.py`/`api.py` reali),
per restare utilizzabile anche per verificare che il protocollo non sia
cambiato lato Areti/Salesforce indipendentemente dal codice
dell'integrazione.

Alla fine salva la risposta completa in `areti_misure_debug.json` (file
locale, escluso da git — contiene POD, codice fiscale e consumi reali,
non va mai condiviso senza ripulirlo prima).

Non è un test automatico (richiede credenziali reali digitate a mano):
utile per iterare più velocemente di quanto permetta la UI di Home
Assistant, e come primo controllo se il login smette di funzionare in
produzione.
