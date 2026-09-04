# HomeAssistant-Contatore

Unofficial meta-integration for Italian electricity distributor meter data in Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://hacs.xyz/)
[![GitHub Release](https://img.shields.io/github/v/release/riccardorossi92/HomeAssistant-Contatore.svg?style=for-the-badge&color=blue)](https://github.com/riccardorossi92/HomeAssistant-Contatore/releases)
[![Integration Usage](https://img.shields.io/badge/dynamic/json?color=41BDF5&style=for-the-badge&logo=home-assistant&label=usage&suffix=%20installs&cacheSeconds=15600&url=https://analytics.home-assistant.io/custom_integrations.json&query=$['contatore_letture'].total)](https://analytics.home-assistant.io/)

> **Disclaimer:** This is an unofficial integration and is not affiliated with or endorsed by ARERA, Duereti, Unareti, E-Distribuzione, or any other distributor in any way.

Integrazione per Home Assistant che, dato il tuo comune, individua
automaticamente il distributore elettrico competente (interrogando
[ARERA](https://www.arera.it/area-operatori/ricerca-operatori) in tempo
reale) e configura di conseguenza l'importazione delle curve di consumo dei
tuoi POD come statistiche esterne, visibili anche nella Energy Dashboard.

Invece di dover sapere in anticipo quale distributore ti serve,
`contatore_letture` lo scopre per te durante la configurazione.

## Distributori supportati

| Distributore | Autenticazione | Azioni |
|---|---|---|
| Duereti | Client ID + Secret ID | `recupera_storico`, `recupera_ticket` |
| Unareti | Client ID + Secret ID | `recupera_storico`, `recupera_ticket` |
| E-Distribuzione | Email + password + OTP | `recupera_storico` |

Per tutti: login, lettura dati, import nella Energy Dashboard e più POD
per configurazione — tutto confermato funzionante. Dettagli sulle azioni
in [Azioni](#azioni) più sotto.

Per i comuni serviti da un distributore non ancora supportato, il wizard di
configurazione permette comunque di selezionarlo manualmente se sai che è
uno di quelli supportati, o si ferma con un messaggio chiaro altrimenti.

### In lavorazione

**Ireti**: login e anagrafica già funzionanti, mancano gli endpoint dei
consumi. Se hai una fornitura Ireti puoi aiutare a completarlo — vedi
[documentation/ireti-protocol.md](documentation/ireti-protocol.md).

### Valutati e non fattibili

**Areti** (Roma e Formello): l'area riservata Areti è un portale di
pratiche/segnalazioni e non espone consumi né curve di carico (verificato
sul route map completo del portale). Per i distributori locali il dato di
misura passa dal SII, accessibile solo via portale del venditore o
Portale Consumi ARERA (SPID/CIE). Dettagli in
[documentation/areti-protocol.md](documentation/areti-protocol.md).

## Come funziona il rilevamento del distributore

1. Selezioni regione → provincia → comune (elenco ISTAT, aggiornato
   automaticamente ad ogni configurazione, con una copia di riserva
   inclusa nell'integrazione nel caso il download non sia disponibile).
2. L'integrazione interroga live la pagina di
   [ricerca operatori ARERA](https://www.arera.it/area-operatori/ricerca-operatori)
   per quel comune, filtrando sui distributori elettrici.
3. In base alla Partita IVA dell'operatore restituito, il wizard prosegue
   con lo step corretto.

## Prerequisiti

I distributori supportati usano meccanismi di autenticazione diversi tra
loro — nessuno è "il caso normale" rispetto agli altri, sono semplicemente
protocolli distinti imposti da ciascun distributore. Espandi la sezione
del tuo distributore per i dettagli.

<details>
<summary><b>Duereti / Unareti (Client ID + Secret ID)</b></summary>

Le API PCF non sono pubbliche in modo libero: vanno abilitate manualmente
dal distributore, che poi invia via email le credenziali (`client_id` e
`secret_id`) da usare in questa integrazione.

1. Accedi al **Portale Clienti Finali (PCF)** del tuo distributore:
   - Duereti: [areaclienti.duereti.it](https://areaclienti.duereti.it/ClientiDueRetiWeb)
   - Unareti: [areaclienti.unareti.it](https://areaclienti.unareti.it/ClientiWeb)
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

</details>

<details>
<summary><b>E-Distribuzione (email + password + OTP)</b></summary>

Nessuna richiesta di abilitazione preventiva: ti servono solo le stesse
credenziali dell'app/area clienti ufficiale E-Distribuzione (email,
password, e il codice OTP che ricevi via email o SMS al momento
dell'accesso — te lo chiede direttamente il wizard di configurazione). Se
il tuo account ha più POD associati, potrai selezionarne più di uno.

</details>

## Installazione

### Tramite HACS (custom repository)

1. HACS → menu (⋮) → **Repository personalizzate**
2. Aggiungi l'URL di questo repository, categoria **Integrazione**
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
   [Prerequisiti](#prerequisiti) sopra)

Dopo la configurazione, puoi aggiungere/rimuovere POD e cambiare l'orario
della richiesta giornaliera in qualsiasi momento da **Configura**
sull'integrazione (Opzioni) — per qualunque distributore.

## Cosa fa una volta configurata

I dati importati sono visibili come **external statistics**
(`contatore_letture:<pod>_energia`) in **Impostazioni → Sistema →
Statistiche**, utilizzabili nella Energy Dashboard, per tutti i
distributori supportati.

**Ogni sera dopo le 19:00** (orario configurabile dalle opzioni) viene
richiesto il giorno precedente. Se non è ancora stato pubblicato finisce
in una coda e viene riprovato nei giorni successivi, così non si creano
buchi nello storico. **Lo storico non viene recuperato automaticamente**:
si richiede con l'azione `recupera_storico` (vedi [Azioni](#azioni) sotto).

Tutte le entità esposte sono diagnostiche — i consumi stanno nelle
statistiche, non in un sensore — raggruppate in un dispositivo "Account"
più uno per ogni POD. Espandi la sezione del tuo distributore per il
dettaglio.

<details>
<summary><b>Entità esposte — Duereti / Unareti</b></summary>

| Entità | Dispositivo | Cosa mostra |
|---|---|---|
| Ultimo import | Account | Fine del periodo dell'ultimo import riuscito |
| Attesa file (minuti) | Account | Da quanto si attende il file; `0` se niente in coda |
| POD configurati | Account | Quanti e quali POD in questa istanza |
| Ultima data disponibile | POD | Ultimo giorno per cui esistono dati importati |
| Consumo ultimo periodo | POD | kWh totali dell'ultimo periodo importato |

*Attesa file* è utile per un'automazione di allerta: se resta alto per ore,
qualcosa si è inceppato. POD e dato fiscale vengono verificati subito in
configurazione: se il distributore non li riconosce, il form non permette
di salvare.

</details>

<details>
<summary><b>Entità esposte — E-Distribuzione</b></summary>

| Entità | Dispositivo | Cosa mostra |
|---|---|---|
| POD configurati | Account | Quanti e quali POD in questa istanza |
| Ultima data disponibile | POD | Ultimo giorno per cui esistono dati importati |
| Consumo ultimo giorno importato | POD | kWh dell'ultimo giorno importato |

</details>

## Azioni

**`contatore_letture.recupera_storico`** — richiede un periodo passato e lo
importa, con un'unica richiesta per l'intero periodo. Il campo
"Configurazione / POD" è un selettore di dispositivo popolato
dinamicamente: la scelta più comoda è farla dall'interfaccia
(**Strumenti per sviluppatori → Azioni**), dove compare come un menu a
tendina con i nomi reali. Le API accettano al massimo 6 mesi per
richiesta; per periodi più lunghi ripeti l'azione su intervalli
consecutivi.

```yaml
action: contatore_letture.recupera_storico
data:
  device_id: <scegli dall'interfaccia, vedi sopra>
  data_da: "2026-02-01"
  data_a: "2026-07-31"
```

Per le sfumature specifiche di ciascun distributore (limite di 6 mesi
documentato o auto-imposto, targeting per singolo POD) vedi
[`documentation/`](documentation/).

**`contatore_letture.recupera_ticket`** — solo Duereti/Unareti
(E-Distribuzione non ha il concetto di ticket): riprende un ticket già
esistente presso il distributore, saltando la richiesta di un nuovo export.

```yaml
action: contatore_letture.recupera_ticket
data:
  ticket: "ENdZS6CausBMlUzrS3as5Q"
  entry_id: <opzionale, se hai più istanze Duereti/Unareti>
```

## Documentazione tecnica

Dettagli di reverse engineering dei protocolli (endpoint, gotcha,
struttura delle risposte), architettura del codice, sviluppo/test e
script disponibili sono in [`documentation/`](documentation/) — non
necessari per usare l'integrazione, utili se vuoi contribuire o capire
come funziona sotto.

Se vuoi aggiungere un nuovo distributore o modificarne uno esistente,
vedi [`CONTRIBUTING.md`](CONTRIBUTING.md).
