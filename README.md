# HomeAssistant-Contatore

Unofficial meta-integration for Italian electricity distributor meter data in Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://hacs.xyz/)
[![GitHub Release](https://img.shields.io/github/v/release/riccardorossi92/HomeAssistant-Contatore.svg?style=for-the-badge&color=blue)](https://github.com/riccardorossi92/HomeAssistant-Contatore/releases)
[![Integration Usage](https://img.shields.io/badge/dynamic/json?color=41BDF5&style=for-the-badge&logo=home-assistant&label=usage&suffix=%20installs&cacheSeconds=15600&url=https://analytics.home-assistant.io/custom_integrations.json&query=$['contatore_letture'].total)](https://analytics.home-assistant.io/)

> **Disclaimer:** This is an unofficial integration and is not affiliated with or endorsed by ARERA, Duereti, Unareti, E-Distribuzione, or any other distributor in any way.

Integrazione per Home Assistant che, dato il tuo comune, individua
automaticamente il distributore elettrico competente (interrogando
[ARERA](https://www.arera.it/area-operatori/ricerca-operatori) in tempo
reale) e configura di conseguenza l'importazione delle curve di consumo del
tuo POD come statistiche esterne, visibili anche nella Energy Dashboard.

È il seguito naturale di [HomeAssistant-Duereti](https://github.com/riccardorossi92/HomeAssistant-Duereti)
e [HomeAssistant-Unareti](https://github.com/riccardorossi92/HomeAssistant-Unareti):
invece di dover sapere in anticipo quale delle due (o quale altra)
integrazione installare, `contatore_letture` lo scopre per te durante la
configurazione.

## Distributori supportati

| Distributore | Stato |
|---|---|
| Duereti | Login, lettura dati e import Energy Dashboard funzionanti (Portale Clienti Finali / PCF) |
| Unareti | Login, lettura dati e import Energy Dashboard funzionanti (Portale Clienti Finali / PCF) |
| E-Distribuzione | Login e lettura dati funzionanti; import Energy Dashboard non ancora disponibile (schema dati della curva di carico non confermato) |

Per i comuni serviti da un distributore non ancora supportato, il wizard di
configurazione permette comunque di selezionarlo manualmente se sai che è
uno di quelli supportati, o si ferma con un messaggio chiaro altrimenti.

## Come funziona il rilevamento del distributore

1. Selezioni regione → provincia → comune (elenco ISTAT, aggiornato
   automaticamente ad ogni configurazione, con una copia di riserva
   inclusa nell'integrazione nel caso il download non sia disponibile).
2. L'integrazione interroga live la pagina di
   [ricerca operatori ARERA](https://www.arera.it/area-operatori/ricerca-operatori)
   per quel comune, filtrando sui distributori elettrici.
3. In base alla Partita IVA dell'operatore restituito, il wizard prosegue
   con lo step corretto (Duereti, Unareti, o selezione manuale se il
   distributore non è ancora supportato o il lookup non è riuscito).

## Prerequisiti

I tre distributori supportati usano meccanismi di autenticazione diversi
tra loro — nessuno dei due è "il caso normale" rispetto all'altro, sono
semplicemente due protocolli distinti imposti da ciascun distributore.

### Duereti / Unareti (Client ID + Secret ID)

Le API PCF non sono pubbliche in modo libero: vanno abilitate manualmente
dal distributore, che poi invia via email le credenziali (`client_id` e
`secret_id`) da usare in questa integrazione.

1. Accedi al **Portale Clienti Finali (PCF)** del tuo distributore:
   - Duereti: `https://areaclienti.duereti.it/ClientiDueRetiWeb`
   - Unareti: `https://areaclienti.unareti.it/ClientiWeb`
2. Assicurati di avere **almeno un'identificazione validata** dal
   backoffice del distributore sul tuo profilo: senza questo passaggio la
   richiesta di abilitazione API non compare nemmeno.
3. Vai nella sezione **"Area POD/PDR: Interruzioni, Misure e servizi"** e
   cerca l'opzione per richiedere l'abilitazione all'uso delle API.
4. Invia la richiesta e attendi l'accettazione.
5. Una volta approvata, riceverai via email **Client ID** e **Secret ID**
   (sono comunque visibili anche nella stessa pagina del portale da cui hai
   fatto la richiesta).
6. Prendi nota anche di:
   - il/i **codice/i POD o PDR** che vuoi monitorare;
   - il **dato fiscale** associato a ciascun POD/PDR (codice fiscale o
     partita IVA a seconda dell'intestatario — richiesto ad ogni chiamata
     insieme al POD).

Questo processo è interamente gestito dal distributore: l'integrazione non
può velocizzarlo né bypassarlo.

### E-Distribuzione (email + password + OTP)

Nessuna richiesta di abilitazione preventiva: ti servono solo le stesse
credenziali dell'app/area clienti ufficiale E-Distribuzione (email,
password, e il codice OTP che ricevi via SMS al momento dell'accesso — te
lo chiede direttamente il wizard di configurazione). Se il tuo account ha
più POD associati, potrai scegliere quale monitorare durante la
configurazione.

## Installazione

### Tramite HACS (custom repository)

1. HACS → menu (⋮) → **Repository personalizzate**
2. Aggiungi `https://github.com/riccardorossi92/HomeAssistant-Contatore`,
   categoria **Integrazione**
3. Installa "Contatore Letture" e riavvia Home Assistant

### Manuale

1. Copia la cartella `custom_components/contatore_letture` nella cartella
   `custom_components` della tua configurazione Home Assistant
2. Riavvia Home Assistant

## Configurazione

1. **Impostazioni → Dispositivi e Servizi → Aggiungi integrazione**, cerca
   **Contatore Letture**
2. Seleziona regione, provincia e comune della fornitura
3. Il distributore viene individuato automaticamente (o selezionato a mano
   se necessario), e ti viene mostrato cosa ti servirà per proseguire
4. Inserisci le credenziali del tuo distributore (vedi
   [Prerequisiti](#prerequisiti) sopra: Client ID/Secret ID per
   Duereti/Unareti, email/password/OTP per E-Distribuzione) — vengono
   validate subito con una chiamata reale alle sue API
5. Per Duereti/Unareti, aggiungi uno o più **POD** con il relativo
   **codice fiscale**; per E-Distribuzione, seleziona il POD tra quelli
   dell'account (se ce n'è più di uno)

## Cosa fa una volta configurata

I dati importati sono visibili come **external statistics**
(`contatore_letture:<pod>_energia`) in **Impostazioni → Sistema →
Statistiche**, utilizzabili nella Energy Dashboard — oggi solo per
Duereti/Unareti (vedi tabella distributori sopra).

### Duereti / Unareti

- **POD e dato fiscale vengono verificati subito in configurazione:** se il
  distributore non li riconosce, il form non permette di salvare.
- **Ogni sera dopo le 19:00** (orario configurabile) viene richiesto il
  giorno precedente. Se non è ancora stato pubblicato finisce in una coda e
  viene riprovato nei giorni successivi, così non si creano buchi nello
  storico.
- **Lo storico non viene recuperato automaticamente:** si richiede con
  l'azione `recupera_storico`.
- Dopo la configurazione puoi aggiungere/rimuovere POD e cambiare l'orario
  della richiesta giornaliera in qualsiasi momento da **Configura**
  sull'integrazione (Opzioni).

#### Entità esposte

Sono tutte diagnostiche — i consumi stanno nelle statistiche, non in un
sensore — e raggruppate in un dispositivo "Account API" più uno per ogni POD.

| Entità | Dispositivo | Cosa mostra |
|---|---|---|
| Ultimo import | Account | Fine del periodo dell'ultimo import riuscito |
| Attesa file (minuti) | Account | Da quanto è in corso l'attesa del file; `0` se non c'è nulla in coda |
| POD configurati | Account | Quanti POD in questa istanza |
| Ultima data disponibile | POD | Ultimo giorno per cui esistono dati importati |
| Consumo ultimo periodo | POD | kWh totali dell'ultimo periodo importato |

*Attesa file* è utile per un'automazione di allerta: se resta alto per ore,
qualcosa si è inceppato.

#### Azioni

**`contatore_letture.recupera_storico`** — richiede un periodo passato e lo
importa. Le API accettano al massimo 6 mesi per volta; per periodi più
lunghi ripeti l'azione su intervalli consecutivi.

```yaml
action: contatore_letture.recupera_storico
data:
  data_da: "2026-02-01"
  data_a: "2026-07-31"
```

**`contatore_letture.recupera_ticket`** — riprende un ticket già esistente,
saltando la richiesta di un nuovo export.

```yaml
action: contatore_letture.recupera_ticket
data:
  ticket: "ENdZS6CausBMlUzrS3as5Q"
```

Entrambe accettano un `entry_id` opzionale se hai più istanze configurate.
Nessuna delle due si applica a E-Distribuzione: il suo protocollo non usa
il modello a ticket/export di Duereti e Unareti.

### E-Distribuzione

- **Aggiornamento ogni 60 minuti**: refresh del token, poi lettura mensile
  (reading + time-of-use) per il POD configurato.
- **Nessun import nella Energy Dashboard per ora** — solo sensori di stato
  (vedi sotto). Vedi la nota nello stato dei distributori sopra.
- Nessuna azione dedicata ancora (niente equivalente di
  `recupera_storico`/`recupera_ticket`: il modello dati è diverso, non
  serve un ticket da riprendere).

#### Entità esposte

Un dispositivo per POD, con un sensore per ogni combinazione
magnitudine/fascia dell'ultima lettura pubblicata, più i picchi di potenza:

| Entità | Cosa mostra |
|---|---|
| Energia attiva / reattiva T1–T6 | Ultima lettura cumulativa pubblicata per fascia |
| Picco di potenza T1–T6 | Ultimo picco di potenza pubblicato per fascia |

## Sviluppo e test

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
```

I test in `test_api.py`, `test_api_errori.py` (pcf_common) e `test_auth.py`
(edistribuzione) non richiedono Home Assistant installato — coprono solo
codice che dipende esclusivamente da `aiohttp` e libreria standard, caricato
direttamente per bypassare i punti della gerarchia di pacchetti che
importano `homeassistant.*`. `test_coordinator_dates.py` richiede invece
`pytest-homeassistant-custom-component`.

Per lanciare solo i test che non richiedono HA:

```bash
pytest tests/pcf_common/test_api.py tests/pcf_common/test_api_errori.py tests/edistribuzione/
```

## Progetti collegati

- [HomeAssistant-Duereti](https://github.com/riccardorossi92/HomeAssistant-Duereti) — integrazione standalone Duereti (dominio separato)
- [HomeAssistant-Unareti](https://github.com/riccardorossi92/HomeAssistant-Unareti) — integrazione standalone Unareti (dominio separato)

`contatore_letture` non sostituisce queste due integrazioni per chi le ha
già installate: usa un dominio Home Assistant proprio
(`contatore_letture`, diverso da `duereti_letture`/`unareti_letture`), quindi
un utente esistente di una delle due può continuare a usarla senza modifiche,
oppure passare a `contatore_letture` come nuova installazione.
