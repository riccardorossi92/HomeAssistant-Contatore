# Sviluppo e test

Serve Python 3.13+ (`pytest-homeassistant-custom-component` tira dentro
Home Assistant, che non supporta più il 3.12). La CI gira su 3.13 e 3.14.

```bash
pip install -r requirements_test.txt
pytest
```

Lint (stessa config della CI, `pyproject.toml`):

```bash
ruff check .
```

## Layout

```
tests/
  conftest.py                        # abilita l'integrazione custom (fixture hass)
  test_config_flow.py                # wizard ARERA + ramo PCF + ramo E-Distribuzione + opzioni + reauth
  test_arera_lookup.py               # parsing dell'HTML ARERA + errori di rete
  test_init_services.py              # helper delle azioni (_trova_coordinator, _risolvi_...)
  pcf_common/                        # Duereti / Unareti
    conftest.py                        # harness: FakePcfApi, make_pcf_coordinator, pcf_io
    test_api.py                         # non richiede HA
    test_api_errori.py                  # non richiede HA
    test_coordinator_dates.py           # funzioni di data isolate
    test_coordinator_coda.py            # coda giorni da riprovare (end-to-end sul coordinator)
    test_coordinator_fuso.py            # il "giorno atteso" segue il fuso di HA
    test_statistics.py                  # aggregazione oraria + cambio ora (DST)
  edistribuzione/
    conftest.py                        # harness: make_edist_coordinator
    test_auth.py                        # non richiede HA
    test_api.py                         # non richiede HA
    test_coordinator_coda.py            # coda per-POD
```

## Test senza Home Assistant installato

`pcf_common/api.py` e `edistribuzione/auth.py`/`api.py` non importano
`homeassistant.*` (solo `aiohttp` + libreria standard). I relativi test
li caricano direttamente, quindi girano anche senza il pacchetto HA:

```bash
pytest tests/pcf_common/test_api.py tests/pcf_common/test_api_errori.py \
       tests/edistribuzione/test_auth.py tests/edistribuzione/test_api.py
```

Tutto il resto richiede `pytest-homeassistant-custom-component`.

Vedi anche [`scripts.md`](scripts.md) per gli strumenti da terminale che
non sono test automatici (richiedono credenziali reali digitate a mano)
ma servono allo stesso scopo di verifica.
