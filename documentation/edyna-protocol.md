# Edyna — ricerca sospesa: cattura insufficiente + tecnologia ostile

**Edyna S.r.l.** (distributore dell'Alto Adige / Südtirol, gruppo
Alperia) — portale distributore `portaledistributore.edyna.net`.

Stato: **non implementabile con i dati disponibili oggi**. Non è un "no"
definitivo come [Areti](areti-protocol.md): qui manca proprio la cattura
utile. Questa scheda serve a non ripartire da zero e a sapere cosa
chiedere per riprovare.

## Verdetto

| | |
|---|---|
| Il portale espone consumi / letture / curve di carico? | **Non verificabile** — la cattura non arriva a nessuna pagina di misura |
| POD associato all'account catturato? | **No** (segnalato dall'utente e confermato dall'HAR) |
| Tecnologia del portale | ASP.NET WebForms tipo *Instant Developer / "TWS"* — endpoint unico a postback, nessuna API JSON |
| Azione | Nessuna implementazione. Riaprire solo con un HAR nuovo, da un account **con POD associato**, che navighi fino alla sezione consumi |

## Cosa c'era nell'HAR (cattura 04/09/2026)

HAR di `portaledistributore.edyna.net`, utente loggato ma **senza POD
associato**.

| | |
|---|---|
| Entry totali | 33, di cui ~13 asset statici (JS/CSS/immagini) |
| Richieste "dati" | **1 sola**: `POST /EIPPUF/EIPPUF/it-IT/dD4/Page/Single.tws` → HTML ~82 KB (pagina di atterraggio post-login) |
| Endpoint JSON / REST | **nessuno** |
| Dati di consumo / lettura / curva | **nessuno** |
| POD nel payload o nelle risposte | nessuno |

Senza un POD associato l'account non può navigare ad alcuna pagina di
fornitura, quindi l'HAR non mostra **se** e **come** Edyna esponga i
consumi. È il primo problema da risolvere prima di qualunque valutazione.

## Perché la tecnologia è un problema a parte

Indizi nell'HAR:

- estensione pagina `.tws`, endpoint unico `.../Page/Single.tws`;
- `ScriptResource.axd` / `WebResource.axd` (ASP.NET WebForms);
- nome applicazione `EIPPUF` nel path;
- token di sessione opaco nel path stesso (`.../dD4/...`).

È il pattern delle applicazioni **Instant Developer / "TWS"**: una web
form stateful dove *tutto* passa da un solo endpoint `Single.tws` via
postback, con stato lato server legato al token di sessione (`dD4`) e a
un viewstate. Conseguenze per un'integrazione:

- **niente API REST/JSON** da chiamare direttamente;
- lo scraping richiede di replicare la sequenza di postback dei form e
  fare parsing di tabelle HTML renderizzate dal server;
- il token di sessione nel path e il viewstate rendono la sessione
  fragile e poco riproducibile.

Rientra nel **caso B** di [`CONTRIBUTING.md`](../CONTRIBUTING.md)
(protocollo tutto nuovo, pacchetto `distributors/edyna/` a sé), ed è più
scomodo del caso Aura/Salesforce di [Areti](areti-protocol.md): lì
almeno gli endpoint dati erano JSON.

## Se un domani si riapre

Serve, in quest'ordine:

1. un account Edyna **con almeno un POD associato**;
2. un HAR nuovo catturato **navigando fino alla sezione consumi /
   letture / curve di carico** (con un grafico effettivamente caricato),
   non solo il login;
3. a quel punto si valuta:
   - se il dato di misura (curva oraria / letture / fasce) è davvero
     presente nel portale;
   - se lo scraping dei postback `Single.tws` è abbastanza stabile da
     reggere un `DataUpdateCoordinator`.

Se al passo 3 il dato non c'è, il verdetto diventa come per Areti: per un
distributore locale la misura del cliente finale passa dal **SII**, non
dal portale del distributore, e le fonti alternative (portale del
venditore, Portale Consumi ARERA solo SPID/CIE) non sono automatizzabili
da Home Assistant.

Finché non si hanno i passi 1–2, non c'è niente da implementare.
