# Edyna — implementato e provato in Home Assistant

**Edyna S.r.l.** (distributore dell'Alto Adige / Südtirol, gruppo
Alperia, P.IVA `02689370217` confermata dalla ricerca ARERA per Bolzano e
Salorno) — portale `portaledistributore.edyna.net`. **Supportato** da
`distributors/edyna/`. Il login è verificato su una cattura (account di prova
senza POD); l'intera catena Utenze → Curve → anno → mese su una seconda
(account con POD, 05/11/2026), rigiocata contro il client vero con un
portale finto che risponde con le pagine registrate. **Provato in un Home Assistant
reale** (v0.13.0, 06/11/2026): import senza errori, giorno da 25 ore corretto. Origine: [issue #19](https://github.com/riccardorossi92/HomeAssistant-Contatore/issues/19).
Il portale espone le **curve orarie e al quarto d'ora** e i consumi mensili
per POD.

## Implementazione

| File | Cosa fa |
|---|---|
| `auth.py` | Login in 4 passi (GET modulo, POST UpdatePanel, GET `Single.tws`, `SecondPostback`) |
| `parse.py` | Funzioni pure sull'HTML: campi del form come li invia il browser, griglia Utenze, selettore Anno, link dei mesi, griglia oraria |
| `api.py` | Navigazione: ogni passo è un POST con tutto il form della pagina corrente; se la pagina non porta il contenuto atteso prova `SecondPostback` (al massimo due volte) e poi solleva un errore che nomina il passo; se la tabella dei mesi arriva vuota ripete lo stesso postback una volta. Timeout di 300 s per richiesta |
| `statistics.py` | 25 colonne → bucket orari UTC, con il mapping dei giorni da 23 e 25 ore |
| `coordinator.py` | Una lettura al giorno dopo un orario casuale (06–22), un mese per volta; il primo ciclo parte in background (può durare minuti) |

Scelte di progetto:

- **Nessuna coda di retry.** Nella cattura l'ultimo giorno era 11 giorni
  prima, con una regola ignota. A ogni richiesta si rilegge il mese
  dell'ultimo dato importato (da una settimana prima) fino a quello in corso
  e si importa tutto ciò che c'è: l'import è idempotente e un buco si chiude
  da solo.
- **Controlli prima di importare**: il mese e il POD riportati dalla pagina
  devono coincidere con quelli chiesti; un giorno con celle vuote, con un
  numero di ore che non torna o a zero in tutte le ore (non pubblicato) non
  viene importato.
- **Solo prelievo elettrico**: immissione, produzione e gas sono ignorati.
- Il parsing HTML gira in un executor.

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

### Il portale è lentissimo, e la prima richiesta risponde vuota

Dalla cattura del 05/11/2026 (tempi di risposta del server):

| Passo | Durata |
|---|---|
| Login, menu Utenze | 0,4 s, 3,5 s |
| Apertura tab Curve (`btnCurve`) | **104 s**, risposta con "Nessun risultato" |
| Stesso postback ripetuto | 84 s, risposta con i 10 mesi dell'anno |
| Click sul mese | 44 s (2026), 38 s (2025) |
| Cambio anno (`ddlAnno`) | **101 s**, "Nessun risultato"; ripetuto: 75 s, 12 mesi |
| Click sul giorno (quarti d'ora) | 1 s |

Quindi: (1) la **prima richiesta di Curve o di un anno nuovo restituisce la
tabella dei mesi vuota** (`<div class="alert alert-danger">Nessun
risultato</div>` al posto dei link) e il **secondo postback identico porta i
dati**; il browser dell'utente l'ha ripetuto a mano. Il client lo ripete una
volta e, se la tabella resta vuota, considera l'anno senza dati. (2) Una
singola richiesta può superare i 100 secondi: timeout a 300 s, primo
aggiornamento in background. Un primo ciclo con due mesi in anni diversi può
quindi durare diversi minuti. Il click sul mese, invece, ha funzionato alla
prima richiesta.

La sequenza completa vista, con il tab Curve aperto:

1. `…mMenu1$FirstLevelMenuRepeater$ctl01$lnkLevelMenu` (Utenze) → pagina
   senza contenuto; `body_ctl00_SecondPostback` → griglia Utenze;
2. `…gvUtenze$ctl02$btnCurve` → tab Curve (vuoto), ripetuto → pieno;
3. `…gvCurveAttiva$ctl02$btnCurve10` → tab Curve orarie del mese;
4. `…TCurve$cCurve$ddlAnno` (postback sul select, valore nuovo nel form) →
   vuoto, ripetuto → pieno; poi di nuovo il mese;
5. facoltativi: `…gvCurveOrarieEE$ctl02$btnCurve` (quarti d'ora del primo
   giorno), `…cCurveOrarie$btnClose` ("Chiudi"),
   `…btnExportQuartiGiornalieri` (immagine, `.x`/`.y`): risponde con un
   file Excel (`application/ms-excel`, un `.xlsx` di circa 7 KB) con i quarti
   d'ora del giorno.

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
  Il codice assume, per il giorno da 25 ore, che la colonna 25 sia la
  seconda 02-03 (mapping in `statistics.py`): il valore della cattura (0,26
  kWh, come le altre ore notturne e non come quelle serali) lo supporta, e
  la seconda cattura lo conferma sul 26/10/2025 (colonna 25 a 0,223 kWh,
  come le ore notturne; nei giorni normali vale 0,0) e **in Home Assistant
  reale**: il grafico del 25/10/2026 ha 25 barre con l'ora ripetuta dopo la
  prima 02–03 e il totale giornaliero (18,97 kWh) coincide con la somma
  della riga. Resta da vedere il
  giorno da 23 ore, mai osservato (29/03/2026 non è in nessuna cattura): si importa solo se l'Ora 3 è
  a zero.
- **Formato numerico**: virgola decimale, nessun separatore delle
  migliaia nei valori catturati.
- **Fasce**: il riepilogo F1/F2/F3 è calcolato dal portale; non serve per
  le statistiche orarie.

## Cosa manca

1. **Il ritardo dei dati**: in HA reale i dati arrivano solo fino al
   25/10 (il 06/11); l'utente ha scritto a Edyna per chiedere la regola.
   L'import riprende da solo quando compaiono giorni nuovi.
2. **Due mesi di fila nello stesso anno** (il click sul mese seguente col
   tab Curve orarie ancora aperto): la cattura ha un solo mese per anno. Se
   il link manca il client riapre Curve, ma costa due richieste lente.
3. **Il comportamento a sessione scaduta** (cookie rifiutato, risposta
   diversa dal login?).
4. **Gli export Excel**: `btnExportQuartiGiornalieri` restituisce un
   `.xlsx` con i quarti d'ora del giorno; contenuto non analizzato (la
   cattura lo contiene in base64, non anonimizzabile). Un export mensile
   eviterebbe il parsing HTML.
5. **Il giorno da 23 ore** (ultima domenica di marzo): mai osservato.
6. **Più POD e POD di produzione/immissione**: le catture hanno un solo POD
   di prelievo.

## Come contribuire

Serve chi ha un'utenza Edyna con una fornitura attiva (POD associato):
configura l'integrazione. Se il wizard o il primo import falliscono, il
messaggio d'errore nomina il passo a cui ci si è fermati (es. "passo
'Curve'"): apri una issue con quel messaggio e il log a livello debug per
`custom_components.contatore_letture`. Una cattura HAR (login → Utenze →
Curve → un mese → un giorno) resta la strada più rapida; vedi
[Aiutare senza scrivere codice](../../CONTRIBUTING.md#aiutare-senza-scrivere-codice-raccolta-dati)
in CONTRIBUTING.md: **non allegare la HAR grezza a una issue
pubblica** (contiene token di sessione e dati personali in chiaro) —
apri prima una issue per concordare come condividerla.
