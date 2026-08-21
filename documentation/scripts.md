# Script disponibili

Due script in `scripts/`, entrambi attivamente in uso.

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
