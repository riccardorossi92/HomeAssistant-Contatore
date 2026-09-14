# V-Reti — stato della ricerca (supporto non ancora implementato)

**V-Reti S.p.A.** (ex Megareti, nata dall'integrazione con Servizi a Rete —
gruppo **AGSM AIM**) — distributore di energia elettrica (e gas) nei
Comuni di **Verona**, **Vicenza** e **Grezzana**. P.IVA `03178060236`
(fonte: registro imprese). **Non ancora supportato.**

Aggiornamento 14/09/2026: prima cattura HAR reale disponibile — login
completo (credenziali, **senza 2FA**), ma da un account **in attesa di
validazione** (registrazione self-service non ancora approvata dal
distributore): stesso punto di partenza "account senza dati da mostrare"
già visto per [Edyna](edyna-protocol.md), ma qui il motivo è noto ed
esplicito (vedi sotto), non solo "nessun POD associato".

## Quadro generale

> [!NOTE]
> L'URL da solo bastava già a riconoscere lo stesso **prodotto Terranova**
> ("PUF" / "EPUF" / "EIPPUF", vedi la nota in
> [deval-protocol.md](deval-protocol.md#quadro-generale)) già confermato
> per Edyna e Deval: stessa estensione pagina `.tws`, stesso schema di
> path `/EPUF/<app>/it-IT/<token>/Page/*.tws`, stesso endpoint unico di
> navigazione `Single.tws` passato come `ReturnUrl`. Qui l'app si chiama
> `PUF` (invece di `EPUF` per Deval o `EIPPUF` per Edyna). Il token di
> sessione nel path è assegnato per sessione, non fisso: `Default` nella
> prima segnalazione (pagina di login non ancora avviata), `D1E` nella
> cattura reale — coerente con `DJY`/`dD4` di Deval/Edyna, token opachi
> diversi ad ogni istanza.
>
> Una ricerca web conferma inoltre che lo stesso PUF (stesso schema di
> URL e stesso manuale di registrazione, PDF quasi identico a quello di
> Deval) è usato anche da altri distributori gas non ancora rilevanti per
> questo repository (Apretigas Nord, Reti di Voghera): è un prodotto
> Terranova riusato da diversi distributori locali indipendenti, non solo
> dal gruppo Alperia (Edyna) e da Deval.

Come Edyna, **non è una SPA**: è ASP.NET WebForms, `ScriptResource.axd`/
`WebResource.axd` standard, tutta la navigazione (login, menu, pagine)
passa da `Single.tws` via postback (`__doPostBack`), stato lato server
tenuto insieme da viewstate + token di sessione nel path.

## Login — verificato con cattura reale (14/09/2026)

A differenza di **Deval**, qui il login **non delega** a un sottodominio
di autenticazione esterno e **non è stato osservato alcun 2FA**: è un
singolo `POST` ASP.NET UpdatePanel standard, direttamente verso
`https://snc.v-reti.it/EPUF/PUF/it-IT/<token>/Page/Login.tws?ReturnUrl=...`
(header `X-MicrosoftAjax: Delta=true`, `X-Requested-With: XMLHttpRequest`),
con i campi `ctl00$ctl00$body$body$cLogin$txtUser` /
`ctl00$ctl00$body$body$cLogin$txtPassword` oltre ai soliti
`__VIEWSTATE`/`__EVENTVALIDATION`/`__PREVIOUSPAGE` e
`__EVENTTARGET=ctl00$ctl00$body$body$cLogin$btnLogin`.

La risposta a quel POST include direttamente un `Set-Cookie` per un
cookie di sessione con **nome a forma di GUID** (non un nome cookie
ASP.NET standard tipo `ASP.NET_SessionId`) e valore esadecimale lungo —
niente JWT di handoff come per Deval, niente redirect verso un altro
host: il cookie basta a mantenere la sessione. Il browser prosegue poi
con una `GET` diretta su `Single.tws` (stesso token di sessione nel
path), che restituisce la pagina applicativa.

Quella pagina esegue **un secondo postback automatico** al caricamento
(`__EVENTTARGET=body_ctl00_SecondPostback`, innescato lato client, non
un click utente) verso lo stesso `Single.tws`: è la risposta di questo
secondo postback a contenere il contenuto vero — messaggio di stato
account e menu completo.

> [!NOTE]
> Cattura fatta con l'export HAR di **Safari/WebKit**
> (`creator.name: "WebKit Web Inspector"`), come già capitato per Deval —
> ma qui, a differenza di quel caso, il `Set-Cookie` **è presente** nella
> risposta del login: non è quindi un limite sistematico di Safari, va
> verificato caso per caso.

### Stato dell'account osservato

L'account usato per la cattura è risultato **in attesa di validazione**:
il pannello messaggi post-login mostra testualmente *"L'utenza
utilizzata per l'accesso risulta in attesa di validazione."* — quindi
nessuna sezione Utenze/Letture/Curve è stata raggiunta, non per
mancanza di POD ma perché la registrazione stessa non è ancora stata
approvata dal distributore (coerente con un processo di registrazione
self-service con verifica manuale, come descritto per Deval).

Il menu principale post-login è comunque visibile ed è confermato
identico nella forma a quello di Deval (stesso prodotto, stesso
`__doPostBack` su `Single.tws` per ogni voce): **Utenze**, **Sportello
On-Line Gas** (con sottovoci Richiesta preventivo Nuovo
Allaccio/Modifica Impianto/Rimozione Impianto, Archivio pratiche),
**Modulistica**, **Sportello On-Line Elettrico** (stesse sottovoci del
Gas), **Comunicazioni utenti MT** (dati tecnici su protezioni/continuità,
non consumi), **TICA**. Più un menu profilo separato: **Profilo Utente**,
**Modifica Password**, **Esci**, e una funzione di ricerca "utenti finali
mandanti" (impersonificazione per chi gestisce più utenze per conto
terzi — funzione business, non rilevante qui).

Non essendoci un POD attivo raggiungibile, **Utenze** non è stata
esplorata oltre il click sulla voce di menu: resta da vedere se al suo
interno la struttura Letture/Curve sia identica a quella documentata per
Deval (probabile, stesso prodotto) una volta disponibile un account
validato con un POD elettrico associato.

## Cosa manca

1. Un account **validato dal distributore** (questa cattura si è fermata
   proprio su quel gradino) **con almeno un POD elettrico associato**,
   per arrivare alle sezioni Letture/Curve sotto "Utenze".
2. Con quell'account: la stessa verifica già fatta per Deval, ovvero se
   l'export delle curve produce un file scaricabile diretto o un flusso
   differito, e in che formato.
3. Conferma che il login **non richieda mai 2FA** (qui non osservato, ma
   su un solo account non si può escludere che dipenda da un'impostazione
   per utenza, come invece è *sempre* obbligatorio per Deval).

## Come contribuire

Serve chi ha una fornitura elettrica V-Reti (Verona, Vicenza o
Grezzana) con un'utenza **già validata e con POD associato** sul Portale
Utente. Cattura HAR manuale (Chrome, Firefox o anche Safari — qui il
cookie di sessione risulta comunque incluso, vedi nota sopra): login +
sezione Utenze/Letture/Curve con almeno un mese che produca un
risultato. Vedi
[Aiutare senza scrivere codice](../CONTRIBUTING.md#aiutare-senza-scrivere-codice-raccolta-dati)
in CONTRIBUTING.md: **non allegare la HAR grezza a una issue pubblica**
(contiene token di sessione e dati personali in chiaro, incluse le
credenziali di login inviate nel POST — va condivisa in privato, mai
online) — apri prima una issue per concordare come condividerla.
