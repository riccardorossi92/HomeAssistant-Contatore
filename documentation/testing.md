# Sviluppo e test

```bash
pip install -r requirements_test.txt
pytest
```

```
tests/
  conftest.py                              # fixture condivise (hass, ecc.)
  pcf_common/                                # test per Duereti/Unareti
    test_api.py                                # nessuna dipendenza da HA
    test_api_errori.py                         # nessuna dipendenza da HA
    test_coordinator_dates.py                  # richiede HA installato
  edistribuzione/                            # test per E-Distribuzione
    test_auth.py                               # nessuna dipendenza da HA
    test_api.py                                # nessuna dipendenza da HA
```

I test in `test_api.py`, `test_api_errori.py` (pcf_common) e
`test_auth.py`, `test_api.py` (edistribuzione) non richiedono Home
Assistant installato — coprono solo codice che dipende esclusivamente da
`aiohttp` e libreria standard, caricato direttamente per bypassare i punti
della gerarchia di pacchetti che importano `homeassistant.*`.
`test_coordinator_dates.py` richiede invece
`pytest-homeassistant-custom-component`.

Per lanciare solo i test che non richiedono HA:

```bash
pytest tests/pcf_common/test_api.py tests/pcf_common/test_api_errori.py tests/edistribuzione/
```

Vedi anche [`scripts.md`](scripts.md) per gli strumenti da terminale che
non sono test automatici in senso stretto (richiedono credenziali reali
digitate a mano) ma servono allo stesso scopo di verifica.
