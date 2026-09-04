# Areti — ricerca conclusa: non fattibile allo stato attuale

Areti (distributore di Roma e Formello, gruppo ACEA) **non è supportabile
con l'approccio di questa integrazione**: il portale del distributore
**non espone i dati di consumo/misura**, e le uniche fonti alternative
non sono automatizzabili.

Questa scheda resta come traccia della ricerca, per non rifarla da capo.

## Verdetto

| | |
|---|---|
| Portale distributore espone consumi/letture/curve? | **No** — verificato sul route map completo del portale (04/09/2026) |
| Fonte alternativa (a) legata al distributore e (b) automatizzabile? | **Nessuna** |
| Azione | Nessuna implementazione. Riaprire solo se Areti aggiunge una sezione consumi al portale, o se compare un accesso non-SPID al Portale Consumi / SII |

## Come ci si è arrivati

### 1. Il portale è Salesforce Experience Cloud (Aura)

Verificato su cattura reale (utente loggato, 04/09/2026):

| | |
|---|---|
| Area riservata | `https://areariservataclienti.areti.it/portaleareti/s/` |
| Piattaforma | Salesforce Experience Cloud, framework **Aura** |
| Org ID | `00D5I000000D0zj` |
| Namespace custom | `ARIA_` (oggetti/classi Areti) |
| Auth | sessione a cookie Community (`sid`, `sid_Client`) + `aura.token` CSRF + `fwuid` (id build framework, cambia a ogni release Salesforce) |
| Endpoint dati | `POST /portaleareti/s/sfsites/aura`, corpo form-encoded con `message`/`aura.context`/`aura.token` |

### 2. Il route map completo del portale non ha nulla di misura

Estratto dai bundle Aura dei componenti (77 route). Raggruppate:

- **Account / auth**: login, logout, registrazione, recupero password,
  modifica email/telefono/password/notifiche, elimina account.
- **Forniture di energia**: `associazione-fornitura`,
  `associazione-pratica`, `associazione-reclamo`, `le-tue-forniture`,
  `le-tue-pratiche`, `monitora-le-tue-richieste`, `i-tuoi-reclami`,
  `dettagli-fornitura`, e `fai-una-richiesta/*` (ammodernamento rete,
  comunicazione avvenuti lavori, prelievi irregolari, prestazioni
  tecnico-commerciali, reclami, richieste installazione contatori 2G,
  segnalazioni al punto di fornitura, segnalazioni pagamenti/rimborsi).
- **POD**: `ricerca-pod`, `/pod/:recordId` (`POD_Detail__c` su oggetto
  `POD__c`), `POD_List__c` — dati tecnici del punto (indirizzo, matricola)
  per aprire richieste, non misure.
- **Illuminazione cimiteriale / pubblica**: solo segnalazioni e reclami.
- **Impianti di produzione**: richieste di connessione, iter ordinario/
  semplificato, monitoraggio richieste.
- **Le tue fatture/preventivi**.

**Nessuna route** per consumi, letture, autolettura, curve di carico,
misure, prelievi-come-dato. Cercando nei bundle le stringhe che una
funzione del genere porterebbe con sé (`kWh`, `curva di carico`,
`fascia`, `autolettura`, `lettura`, `misura`) → **zero occorrenze reali**
(solo label di emoji e simili).

> Gli unici hit su "consumption" nei bundle sono controller **standard di
> piattaforma Salesforce** (`aura://ConsumptionApiController`,
> `ConsumptionSchedule`, tag di CRM Analytics): fanno parte di ogni sito
> Experience Cloud, non c'entrano con i consumi elettrici.

Il fatto che l'account di test non avesse POD associati non cambia il
verdetto: mancano proprio le *pagine* e i *componenti* di misura, non
solo i dati.

### 3. Perché è così: il dato passa dal SII, non dal distributore

Per un distributore locale il dato di misura del cliente finale non
transita dal portale del distributore ma dal **SII (Sistema Informativo
Integrato)**, che lo espone:

1. al **venditore** del cliente (portale del proprio fornitore) —
   cambia da fornitore a fornitore, fuori dallo scopo di una
   meta-integrazione basata sul *distributore*;
2. al cliente tramite il **Portale Consumi ARERA** (`consumienergia.it`,
   Acquirente Unico) — area privata **solo SPID o CIE**, login
   interattivo non automatizzabile da Home Assistant. Coprirebbe *tutti*
   i distributori insieme, ma l'autenticazione lo rende inutilizzabile
   qui.

## Se un domani si riapre

Servirebbe una di queste due cose:

- Areti aggiunge una sezione consumi/curve di carico al portale
  `areariservataclienti.areti.it` → allora si rifà una cattura HAR di
  quella sezione e si valuta uno scraping Aura (pacchetto
  `distributors/areti/`, caso B di `CONTRIBUTING.md`; note su auth e
  fragilità `fwuid` nella cronologia git di questo file);
- compare un accesso programmatico (non SPID) al Portale Consumi / SII →
  a quel punto ha senso un distributore "SII" unico invece di uno per
  operatore.

Finché non succede una delle due, non c'è niente da implementare.
