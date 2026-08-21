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

Ogni distributore (o gruppo di distributori che condividono un
protocollo, come Duereti/Unareti) vive nel proprio pacchetto sotto
`distributors/`, con la stessa forma: `api.py` (client HTTP), `auth.py`
se serve un login separato dalle sole credenziali API,
`coordinator.py` (polling + logica di retry), `sensor.py` (entità
diagnostiche), `statistics.py` (import delle curve nella Energy
Dashboard), `const.py`. Il resto del pacchetto (`config_flow.py`,
`__init__.py`, `sensor.py` in cima) fa da orchestratore comune, senza
logica specifica di un singolo distributore al suo interno — quella
vive tutta dentro il pacchetto del distributore.

Per i dettagli specifici di ciascun protocollo, vedi
[`pcf-protocol.md`](pcf-protocol.md) (Duereti/Unareti) e
[`edistribuzione-protocol.md`](edistribuzione-protocol.md).

## Prima di usarlo su un'installazione reale

Consigliato: un primo giro di test manuale del wizard end-to-end
(regione → provincia → comune → lookup ARERA → credenziali → POD) prima
di fare affidamento sui servizi `recupera_ticket`/`recupera_storico`.
