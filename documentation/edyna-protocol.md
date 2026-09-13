# Edyna — stato della ricerca (supporto non ancora implementato)

**Edyna S.r.l.** (distributore dell'Alto Adige / Südtirol, gruppo
Alperia) — portale `portaledistributore.edyna.net`. **Non ancora
supportato.**

Non è noto se il portale esponga dati di misura del distributore (curva
di carico / letture), perché l'unica cattura disponibile viene da un
account **senza POD associato**: non arriva a nessuna pagina di
fornitura o di consumo, quindi non conferma né esclude nulla — stesso
punto di partenza di [Ireti](ireti-protocol.md) e
[SET Distribuzione](set-distribuzione-protocol.md) prima che arrivasse
una cattura con POD.

## Quadro generale

Il portale **non è una SPA** con un bundle JS ispezionabile (a
differenza di Ireti e SET Distribuzione, dove si sono trovati endpoint
candidati analizzando staticamente il JavaScript anche senza una
risposta reale). È un'applicazione **ASP.NET WebForms**, riconoscibile
dagli indizi nella cattura:

- estensione pagina `.tws`, endpoint unico
  `/EIPPUF/EIPPUF/it-IT/dD4/Page/Single.tws`;
- `ScriptResource.axd` / `WebResource.axd` (infrastruttura standard
  ASP.NET AJAX);
- un token di sessione nel path stesso (`dD4`).

È il pattern **"Instant Developer"**: tutta la navigazione (login,
menu, pagine) passa da un solo endpoint `Single.tws` via postback, con
lo stato lato server tenuto insieme dal token di sessione nel path e da
un viewstate nella risposta HTML. Significa che qui **non c'è un
bundle JS da leggere per indovinare gli endpoint dati** come fatto per
Ireti/SET: l'unico modo per sapere cosa espone il portale è navigarci
davvero (con un account che abbia qualcosa da mostrare) e guardare cosa
richiede/risponde `Single.tws` pagina per pagina. Non è di per sé un
motivo per pensare che i consumi non ci siano — è solo un tipo di
portale dove l'analisi statica non aiuta, va fatta la cattura.

> [!NOTE]
> **[Deval](deval-protocol.md)** (Valle d'Aosta) usa lo stesso prodotto:
> app `EIPPUF` qui, `EPUF` là, entrambe targate `Terranova — Innovations
> for Utilities` (footer `Copyright 2018 Terranova`), stesso schema di
> path `.../it-IT/<token>/Page/*.tws`. Il manuale pubblico del PUF Deval
> conferma che il prodotto espone letture mensili e curve orarie per POD
> — se in futuro si sblocca una cattura sull'uno, vale la pena
> ricontrollare l'altro: stesso vendor, probabilmente stesso schema di
> postback ed export dati.

## Cosa c'era nell'unica cattura disponibile (04/09/2026)

Account loggato, **senza POD associato**.

| | |
|---|---|
| Entry totali | 33, di cui ~13 asset statici (JS/CSS/immagini) |
| Richieste verso `Single.tws` | **1 sola**: `POST` → HTML ~82 KB (pagina di atterraggio post-login) |
| Endpoint dati (JSON o altro) | nessuno osservato |
| Consumi / letture / curve di carico | nessuno — la cattura non arriva a nessuna pagina di fornitura |

## Cosa manca

1. Un account **con almeno un POD associato**, per poter navigare fino
   a un'eventuale sezione consumi/letture/curve di carico.
2. Una cattura HAR fatta **navigando fino a quella sezione** (non solo
   il login), con **Chrome o Firefox** DevTools (Network → Preserve
   log attivo *prima* di navigare → "Export HAR with content", per
   avere i body delle risposte e non solo gli header).
3. Con quella cattura: leggere le richieste POST verso `Single.tws` in
   quella fase di navigazione (parametri del postback, e cosa cambia
   nell'HTML di risposta) per capire se il dato è presente e in che
   forma.

## Come contribuire

Serve chi ha un'utenza Edyna con una fornitura attiva (POD associato).
Cattura HAR manuale: login + navigazione fino alla pagina consumi/letture
(se esiste, con un eventuale grafico caricato). Vedi
[Aiutare senza scrivere codice](../CONTRIBUTING.md#aiutare-senza-scrivere-codice-raccolta-dati)
in CONTRIBUTING.md: **non allegare la HAR grezza a una issue
pubblica** (contiene token di sessione e dati personali in chiaro) —
apri prima una issue per concordare come condividerla.
