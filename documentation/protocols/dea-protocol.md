# DEA — stato della ricerca (supporto non ancora implementato)

**DEA — Distribuzione Elettrica Adriatica S.p.A.** (sede a Osimo, AN —
P.IVA `02605800420`) — distributore di energia elettrica in alcuni
Comuni di **Marche** (Osimo, Recanati, …), **Abruzzo** (Ortona),
**Liguria** (Sanremo) e, più di recente, della provincia di **Brescia**.
**Non ancora supportato.**

Aggiornamento 04/10/2026: due catture HAR reale disponibile — login
completo (credenziali, **senza 2FA**) da un account **attivo** (nessun
messaggio di "attesa di validazione" come per [V-Reti](v-reti-protocol.md))
ma **senza alcuna utenza associata**: la seconda cattura arriva fino
alla pagina Utenze (griglia con colonne Letture e Curve), che però
mostra "Nessun risultato".

## Quadro generale

> [!NOTE]
> Quarta istanza confermata dello stesso **prodotto Terranova PUF**
> (vedi [deval-protocol.md](deval-protocol.md#quadro-generale)) dopo
> Edyna, Deval e V-Reti: footer "Engineered by Terranova", versione
> **6.4.6.0**, stessa estensione `.tws`, stesso schema di path
> `/EPUF/<app>/it-IT/<token>/Page/*.tws`, stesso `Single.tws` come
> `ReturnUrl`. Qui l'app si chiama `PROD` e il token nel path è `DG4`
> (coerente con `D1E`/`DJY`/`dD4` degli altri). Il token compare anche in
> `/EPUF/Frontend/customs/DG4/custom.js` e in
> `Images.ashx?...&tmp=DG4`, quindi sembra identificare l'istanza/tema
> più che la sessione — confermato dalla seconda cattura (vedi sotto).

- Portale: `https://portale.deaelettrica.it/EPUF/PROD/it-IT/DG4/Page/Login.tws`
- Raggiungibile dal sito istituzionale (`www.deaelettrica.it/clienti/` →
  "Portale Clienti" → Accedi / registrati qui).
- Registrazione **self-service online** come "Persona fisica" (nome,
  cognome, codice fiscale, email, telefono) o "Soggetto giuridico" —
  nessun modulo cartaceo per i privati.
- Il portale gestisce anche servizi gas (filtro "Codice PDR / Codice POD"
  nella ricerca Utenze), anche se il menu osservato riporta solo lo
  Sportello On-Line Elettrico.

## Login — verificato con cattura reale (04/10/2026)

Identico a [V-Reti](v-reti-protocol.md#login--verificato-con-cattura-reale-14092026):

1. `POST .../Page/Login.tws?ReturnUrl=%2fEPUF%2fPROD%2fit-IT%2fDG4%2fPage%2fSingle.tws`
   come UpdatePanel ASP.NET (`X-MicrosoftAjax: Delta=true`,
   `X-Requested-With: XMLHttpRequest`), con
   `ctl00$ctl00$SManager=ctl00$ctl00$body$body$cLogin$upLogin|ctl00$ctl00$body$body$cLogin$btnLogin`,
   `__VIEWSTATE`/`__VIEWSTATEGENERATOR`/`__PREVIOUSPAGE`/`__EVENTVALIDATION`,
   `ctl00$ctl00$body$body$cLogin$txtUser`,
   `ctl00$ctl00$body$body$cLogin$txtPassword`, `__ASYNCPOST=true`,
   `ctl00$ctl00$body$body$cLogin$btnLogin=Accedi`.
2. La risposta (formato delta `1|#||4|...`) contiene `pageRedirect` verso
   `Single.tws` e un `Set-Cookie` con **nome a forma di GUID** e valore
   esadecimale lungo — unico cookie di sessione, nessun JWT, nessun
   redirect verso altri host, **nessun 2FA**.
3. `GET .../Page/Single.tws` con quel cookie.
4. Secondo postback automatico `POST .../Page/Single.tws` con
   `__EVENTTARGET=body_ctl00_SecondPostback` (più i campi vuoti della
   ricerca "utenti finali mandanti"), che restituisce menu e home.

Cattura fatta con l'export HAR di **Safari/WebKit**: il `Set-Cookie` del
login **è presente**, come per V-Reti.

### Menu post-login

Stesso schema `__doPostBack` su `Single.tws`
(`ctl00$body$ctl00$mMenu1$FirstLevelMenuRepeater$ctlNN$lnkLevelMenu`):
**Utenze**, **Sportello On-Line Elettrico** (preventivi nuovo
allaccio/modifica/rimozione, archivio pratiche), **Comunicazioni**
(richiesta informazioni elettricità e archivio), **Comunicazioni utenti
MT** (protezioni, continuità, buchi di tensione — dati tecnici, non
consumi), **TICA** (pratiche di connessione produttori, Delibere 109/21,
385/25, 421/14, 786/16, 540/21). Menu profilo: **Profilo Utente**,
**Modifica Password**, **Esci**, più la ricerca "utenti finali mandanti".

Rispetto a V-Reti manca lo Sportello On-Line Gas e la Modulistica.
Nessuna voce di menu di primo livello per Letture/Curve: come per gli
altri PUF ci si aspetta che stiano **dentro Utenze**, per singolo POD.

### Pagina Utenze — verificata con seconda cattura (04/10/2026)

Navigazione: `POST Single.tws` con
`__EVENTTARGET=ctl00$body$ctl00$mMenu1$FirstLevelMenuRepeater$ctl01$lnkLevelMenu`
(prima voce di menu = Utenze), che risponde con la pagina ancora senza
contenuto; il browser rilancia poi da solo il solito
`__EVENTTARGET=body_ctl00_SecondPostback` ed è **quella** risposta a
contenere il form Utenze. Ogni cambio pagina costa quindi due POST.

Form di ricerca (prefisso
`ctl00$body$ctl00$ctl00$tcListUtenze$TList$cUFListUtenze$`):

| Campo | Nome | Valori |
|---|---|---|
| Servizio | `ddlServizio` | `""` (tutti), `E` = Energia Elettrica |
| Codice PDR / POD | `txtCodute` | testo libero |
| Ruolo | `ddlRuolo` | `""`, `E` = Prelievo, `C` = Immissione, `P` = Produzione, `X` = Assorbimento, `Y` = Rilascio |
| Cerca | `btnCerca` | `Cerca` |

Colonne della griglia: Servizio, Codice utenza, Ruolo, Venditore,
Codice Misuratore, Indirizzo, Città/Provincia, Stato, più tre colonne
azione da 60px: **Dettaglio**, **Letture**, **Curve**. Quindi letture e
curve sono raggiungibili per riga (per POD e ruolo) direttamente dalla
griglia. Legenda: interruzioni programmate, interruzioni subite
nell'anno, diritto a rimborsi/indennizzi.

Con l'account della cattura la griglia è vuota ("Nessun risultato") già
al primo caricamento, senza premere Cerca.

Il token `DG4` nel path è **identico in due sessioni distinte** (prima e
seconda cattura): è fisso per l'istanza DEA, non per sessione, quindi
l'URL di login si può scrivere in chiaro nel codice.

## Cosa manca

1. Un account **con almeno un POD elettrico DEA associato**, per
   arrivare alle sezioni Letture/Curve sotto "Utenze". Da capire anche
   come si associa un POD a un account self-service (automatico tramite
   codice fiscale dell'intestatario? richiesta manuale?).
2. Con quell'account: se l'export delle curve produce un file
   scaricabile diretto o un flusso differito, e in che formato.
3. Cosa restituiscono i pulsanti **Letture** e **Curve** di una riga
   (postback con argomento di riga? pagina dedicata? download?).

## Come contribuire

Serve chi ha una fornitura elettrica DEA con il POD visibile in
**Utenze** sul Portale Clienti. Cattura HAR manuale (Chrome, Firefox o
Safari): login → Utenze → dettaglio del POD → letture/curve con almeno un
mese che produca un risultato. Vedi
[Aiutare senza scrivere codice](../CONTRIBUTING.md#aiutare-senza-scrivere-codice-raccolta-dati)
in CONTRIBUTING.md: **non allegare la HAR grezza a una issue pubblica**
(contiene la password inviata nel POST di login, cookie di sessione e
dati personali) — apri prima una issue per concordare come condividerla.
