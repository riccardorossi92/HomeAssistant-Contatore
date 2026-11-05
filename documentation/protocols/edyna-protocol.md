# Edyna — stato della ricerca (dati di misura confermati, supporto non ancora implementato)

**Edyna S.r.l.** (distributore dell'Alto Adige / Südtirol, gruppo
Alperia) — portale `portaledistributore.edyna.net`. **Non ancora
supportato**, ma dal 05/11/2026 ([issue #19](https://github.com/riccardorossi92/HomeAssistant-Contatore/issues/19))
c'è una cattura da un account **con un POD attivo** che conferma che il
portale espone le **curve orarie e al quarto d'ora** e i consumi mensili
per POD. Il login è confermato (nome utente e password, senza 2FA); resta
da catturare la navigazione tra Utenze e curva (vedi [Cosa manca](#cosa-manca)).

## Quadro generale

Il portale **non è una SPA** con un bundle JS ispezionabile (a
differenza di Ireti e SET Distribuzione, dove si sono trovati endpoint
candidati analizzando staticamente il JavaScript). È un'applicazione
**ASP.NET WebForms**, riconoscibile dagli indizi nella cattura:

- estensione pagina `.tws`, endpoint unico
  `/EIPPUF/EIPPUF/it-IT/dD4/Page/Single.tws`;
- `ScriptResource.axd` / `WebResource.axd` (infrastruttura standard
  ASP.NET AJAX);
- un token nel path stesso (`dD4`).

È il pattern **"Instant Developer"**: tutta la navigazione (login,
menu, pagine) passa da un solo endpoint `Single.tws` via postback, con
lo stato lato server tenuto insieme da `__VIEWSTATE` /
`__EVENTVALIDATION` nell'HTML. **Non esiste un endpoint JSON**: i dati
sono dentro l'HTML della risposta, in tabelle da leggere con un parser
HTML, e ogni passo di navigazione è un `POST` che rimanda tutto il form.

> [!NOTE]
> **[Deval](deval-protocol.md)** (Valle d'Aosta), **[V-Reti](v-reti-protocol.md)**
> (Verona/Vicenza) e **[DEA](dea-protocol.md)** usano lo stesso prodotto
> Terranova PUF; elenco e cosa hanno in comune in
> [terranova-puf.md](terranova-puf.md). Edyna è la **prima istanza con un
> POD associato**: quello che si impara qui vale con buona probabilità
> anche per le altre.

## Login — verificato con cattura reale (05/11/2026)

Cattura fatta con un **account di prova senza POD**, dall'inizio del login.
È identico a [V-Reti](v-reti-protocol.md#login--verificato-con-cattura-reale-14092026)
e [DEA](dea-protocol.md#login--verificato-con-cattura-reale-04102026):

1. `POST /EIPPUF/EIPPUF/it-IT/dD4/Page/Login.tws?ReturnUrl=%2fEIPPUF%2fEIPPUF%2fit-IT%2fdD4%2fPage%2fSingle.tws`
   come UpdatePanel ASP.NET (`X-MicrosoftAjax: Delta=true`,
   `X-Requested-With: XMLHttpRequest`, `Content-Type:
   application/x-www-form-urlencoded; charset=utf-8`) con
   `ctl00$ctl00$SManager=ctl00$ctl00$body$body$cLogin$upLogin|ctl00$ctl00$body$body$cLogin$btnLogin`,
   `__VIEWSTATE`, `__VIEWSTATEGENERATOR`, `__PREVIOUSPAGE`,
   `__EVENTVALIDATION` (letti dalla pagina `Login.tws`, che nella
   cattura non c'è: era già caricata),
   `ctl00$ctl00$body$body$cLogin$txtUser`,
   `ctl00$ctl00$body$body$cLogin$txtPassword`, `__ASYNCPOST=true`,
   `ctl00$ctl00$body$body$cLogin$btnLogin=Accedi`.
2. Se le credenziali sono giuste, la risposta (formato delta
   `1|#||4|52|pageRedirect||%2fEIPPUF%2f...Single.tws|`) contiene un
   `pageRedirect` verso `Single.tws` e un `Set-Cookie` HttpOnly, `Path=/`,
   con **nome a forma di GUID** e valore esadecimale lungo (circa 2400
   caratteri): è l'unico cookie di sessione.
3. `GET .../Page/Single.tws` con quel cookie.
4. Secondo postback automatico `POST .../Page/Single.tws` con
   `__EVENTTARGET=body_ctl00_SecondPostback` e i campi vuoti della
   ricerca "utenti finali mandanti"; la risposta contiene menu e home.

**Il campo è un nome utente, non un'email.** Due tentativi con
l'indirizzo e-mail dell'account sono falliti; il terzo, con il nome
utente, è riuscito. Il fallimento ha risposta `200` **senza** `Set-Cookie`
e senza `pageRedirect`, con il messaggio *"Errore Autenticazione: Credenziali
di accesso non valide"* dentro il pannello `cMessageBox` (la stessa
stringa compare anche negli script della pagina anche quando il login
riesce, quindi per riconoscere l'errore non basta cercarla: serve
controllare la presenza di `pageRedirect`).

**Nessun 2FA**: né codice via SMS/e-mail, né app authenticator, né altri
passaggi dopo la password. La sessione è un cookie, non un token.

Menu dopo il login (`mMenu1$FirstLevelMenuRepeater$ctlNN$lnkLevelMenu`):
`ctl01` **Utenze**, `ctl02` Sportello On-Line Elettrico, `ctl03`
Comunicazioni, `ctl04` Comunicazioni utenti MT, `ctl05` TICA, `ctl06`
Modulistica. Con un account senza POD la pagina di atterraggio non mostra
la griglia Utenze.

## Navigazione e dati — verificati su cattura reale (05/11/2026)

Cattura fatta con Firefox: 34 richieste, di cui **una sola** verso
`Single.tws` (un `POST` che restituisce l'HTML della pagina Utenze con
il tab Curve orarie aperto, ~250 KB). Il resto sono asset statici. I
passi precedenti non sono nel file, ma ID e `__doPostBack` nell'HTML
permettono di ricostruirli.

### 1. Pagina Utenze

Griglia `tcListUtenze$TList$cUFListUtenze$gvUtenze` con colonne Servizio,
Codice utenza (POD), Ruolo (Prelievo/Immissione/Produzione/…), Venditore,
Codice Misuratore, Indirizzo, Città/Provincia, Stato e tre azioni per
riga, ognuna un `__doPostBack` con l'indice di riga (`ctl02` = prima riga):

| Azione | `__EVENTTARGET` (prefisso `ctl00$body$ctl00$ctl00$tcListUtenze$TList$cUFListUtenze$gvUtenze$ctl02$`) |
|---|---|
| Dettaglio | `btnDettaglio` |
| Letture | `btnLetture` |
| Curve | `btnCurve` |

Sulla stessa pagina c'è anche una **"Esportazione massiva dati di
misura"** (pannello con Dal/Al, Servizio, Tipo di misura Curve/Letture,
pulsanti `btnShowMassiveExport` / `btnDoMassiveExport`): non catturata,
non si sa se produca un file o un flusso differito.

Il tab Curve compare accanto a Utenze come tab dinamici
(`TList`, `TCurve`, `TCurveOrarie`; `ClientState` con
`ActiveTabIndex` e `TabState`, es. `{"ActiveTabIndex":2,"TabState":[true,true,true]}`).

### 2. Tab Curve — consumi mensili

Aperto da `btnCurve`. Contiene un selettore **Anno**
(`TCurve$cCurve$ddlAnno`, con gli ultimi anni disponibili) e, per ogni tipo di energia, una
riga con i dodici mesi in kWh (formato italiano, virgola decimale, punto
come separatore di migliaia assente):

- **Energia Attiva** (`gvCurveAttiva`, pulsanti mese
  `btnCurve1` … `btnCurve12`);
- Energia Reattiva Induttiva Prelevata (`gvCurveReattiva`);
- Energia Reattiva Induttiva Immessa e Capacitiva Immessa
  (`gvCurveReattivaInduttivaScambio` / `…CapacitivaScambio`, qui
  "Nessun risultato" perché il POD è solo di prelievo).

Ogni tabella ha un pulsante di export Excel
(`btnExportExcelConsumiMensili…`), non catturato. Il valore di un mese è
un link: `btnCurveN` apre le curve orarie di quel mese. Il valore del mese
in corso differiva leggermente dalla somma delle ore (di pochi decimi
a ottobre): il consumo mensile probabilmente viene dalle
letture, non dalla somma della curva.

### 3. Tab Curve orarie — la curva

Titolo "Consumi del mese: MM/AAAA". Contiene:

1. **Dati caratteristici della curva**: per fascia F1/F2/F3, energia
   totale, oraria massima/minima/media, percentuale e picco di potenza.
   Il totale mese coincide con la somma delle celle orarie.
2. **Dati di misura orari** (`gvCurveOrarieEE`): una riga per giorno,
   prima colonna la data `GG/MM/AAAA` (anche link, vedi sotto), poi
   **25 colonne** "Ora 1 … Ora 25" con etichette `00 — 01` … `23 — 24`
   e `02 — 03` per l'Ora 25. Valori in kWh con 3 decimali e virgola.
3. **Dati per quarto d'ora**: cliccando la data di un giorno
   (`gvCurveOrarieEE$ctlNN$btnCurve`, con `ctl02` = primo giorno del mese)
   la risposta riempie un pannello nascosto `pnlQuartiOrari`
   ("Curve al quarto d'ora di giovedì 01 ottobre 2026") con una tabella
   ora × quarti (colonne `15 30 45 00`). Esistono anche export Excel
   (`btnExportExcelOrari`, `btnExportExcelQuartiOrariMese`,
   `btnExportQuartiGiornalieri`), non catturati.

Il `POST` catturato è proprio il click sul primo giorno: campi del form
(`__EVENTTARGET`, `__VIEWSTATE`, `__VIEWSTATEGENERATOR`,
`__EVENTVALIDATION`, `…ClientState`, `ddlAnno=2026`, più i campi vuoti di
ricerca) con `Content-Type: application/x-www-form-urlencoded`, senza
header `X-MicrosoftAjax`: **postback completo, non UpdatePanel**, e la
risposta è la pagina intera.

### Cose osservate sui dati

- **Granularità**: oraria e al quarto d'ora; più che sufficiente per le
  statistiche orarie di Home Assistant.
- **Ritardo di pubblicazione**: la cattura è del 05/11/2026 ma l'ultimo
  giorno presente del mese di ottobre è il **25/10/2026**, cioè 11 giorni
  prima. Non si sa se sia un ritardo fisso, una cadenza (settimanale?) o
  dipenda dal POD. Da misurare con più catture.
- **Giorno da 25 ore**: il 25/10/2026 (fine ora legale) la riga ha
  valori in tutte e 25 le colonne; negli altri giorni l'Ora 25 vale
  `0,000`. La cattura non dice **in che posizione** cade l'ora
  ripetuta: l'Ora 25 è etichettata `02 — 03`, come l'Ora 3, quindi
  probabilmente è la seconda occorrenza di quell'ora messa in coda, ma
  va verificato con i consumi reali di quel giorno prima di assegnare i
  valori. Il giorno da 23 ore (29/03) non è nella cattura.
- **Formato numerico**: virgola decimale, nessun separatore delle
  migliaia nei valori catturati.
- **Fasce**: il riepilogo F1/F2/F3 è calcolato dal portale; non serve per
  le statistiche orarie.

## Cosa manca

1. **I passi di navigazione tra login e curva**, con un account con POD:
   `Utenze → Curve → mese`, incluso come si cambia anno o mese (`ddlAnno`
   provoca un postback?) e se servono i due postback consecutivi visti su
   DEA. Gli identificativi dei controlli sono noti, mancano le
   risposte intermedie che portano `__VIEWSTATE` ed `__EVENTVALIDATION`.
2. **Il comportamento a sessione scaduta** (cookie rifiutato, risposta
   diversa dal login?).
3. **Cosa restituiscono gli export Excel** (curva mensile, quarti,
   esportazione massiva): un file scaricabile permetterebbe di evitare il
   parsing HTML e di coprire un intervallo ampio in una richiesta.
4. **Più POD e POD di produzione/immissione**: la cattura ha un solo POD
   di prelievo.

## Come contribuire

Serve chi ha un'utenza Edyna con una fornitura attiva (POD associato).
Cattura HAR manuale: login →
Utenze → Curve → un mese → un giorno, poi (se possibile) un export
Excel. Vedi
[Aiutare senza scrivere codice](../../CONTRIBUTING.md#aiutare-senza-scrivere-codice-raccolta-dati)
in CONTRIBUTING.md: **non allegare la HAR grezza a una issue
pubblica** (contiene token di sessione e dati personali in chiaro) —
apri prima una issue per concordare come condividerla.
