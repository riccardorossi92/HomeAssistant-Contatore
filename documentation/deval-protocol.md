# Deval — stato della ricerca (supporto non ancora implementato)

**Deval S.p.A. a s.u.** (distributore di energia elettrica in **Valle
d'Aosta**, sede ad Aosta) — portale **PUF (Portale Utente Finale)** su
`epuf.devalspa.com/EPUF`. **Non ancora supportato.**

P.IVA (da footer di [devalspa.it](https://www.devalspa.it)) = `IT 01013210073`.

Aggiornamento 14/09/2026: prima cattura HAR reale disponibile — login
completo (credenziali + 2FA), ma da un account **senza POD associato**
(stesso punto di partenza di [Edyna](edyna-protocol.md) e
[Ireti](ireti-protocol.md) prima che arrivasse una cattura con POD). A
differenza di quei casi, qui il login stesso è ormai completamente
verificato passo per passo (vedi sotto) — resta solo da vedere la sezione
Utenze/Curve con un account che abbia qualcosa da mostrare. Le altre fonti
usate restano il sito [devalspa.it](https://www.devalspa.it) e il
**manuale utente ufficiale del PUF** (PDF pubblico, scaricabile senza
login dal link "Manuale del PUF" in fondo alla pagina `/login`, letto
13/09/2026).

## Quadro generale

Il portale **non è una SPA** con un bundle JS ispezionabile (a differenza
di Ireti e SET Distribuzione): è un'applicazione **ASP.NET WebForms**,
riconoscibile dagli stessi indizi già visti per Edyna — estensione pagina
`.tws`, endpoint di navigazione unico
(`/EPUF/EPUF/it-IT/DJY/Page/Login.tws?ReturnUrl=...Single.tws`),
`ScriptResource.axd`/`WebResource.axd` (infrastruttura ASP.NET AJAX
standard), token di sessione nel path (`DJY`).

> [!NOTE]
> Non è solo lo stesso *pattern* di Edyna: è probabilmente lo stesso
> **prodotto**. Il footer della pagina di login riporta `Copyright 2018
> Terranova` e il logo `TERRANOVA — Innovations for Utilities` compare sia
> sulla pagina di login live (versione `6.4.6.0`) sia negli screenshot del
> manuale (versione `4.1.8.1`) — un vendor italiano di portali self-service
> per utility. Il nome app di Edyna, `EIPPUF`, si decifra ora come
> **"Enterprise Integration Platform" + "PUF"** (il titolo della scheda
> browser su `epuf.devalspa.com` è letteralmente "Enterprise Integration
> Platform") — lo stesso prodotto Terranova, solo con un nome-istanza
> diverso (`EPUF` per Deval, `EIPPUF` per Edyna) e un token di sessione
> diverso (`DJY` vs `dD4`). Se in futuro si sblocca l'uno, vale la pena
> ricontrollare l'altro: stesso vendor, probabilmente stesso schema di
> postback e — plausibilmente — stessa forma di export dati. Terza
> istanza riconosciuta (14/09/2026, dalla sola forma dell'URL, non ancora
> verificata con una cattura): **[V-Reti](v-reti-protocol.md)** (Verona/
> Vicenza), app `PUF` — vedi quel documento.

Significa, come per Edyna, che **non c'è un bundle JS da leggere per
indovinare gli endpoint dati**: l'unico modo per sapere davvero come
risponde il portale è navigarci con un account vero e guardare le
richieste POST verso l'endpoint `Single.tws` pagina per pagina.

## Login — verificato con cattura reale (14/09/2026)

Da `https://www.devalspa.it/login`, il pulsante "Accedi" del box "Accesso
portale PUF" punta a `https://epuf.devalspa.com/EPUF` (`target="_blanck"`,
apre una nuova scheda). La root `/EPUF` senza path reindirizza al sito
marketing `devalspa.it`; l'URL che funziona davvero è
`https://epuf.devalspa.com/EPUF/EPUF/it-IT/DJY/Page/Login.tws?ReturnUrl=...`.

La pagina di login espone due percorsi: **Form** (identificativo +
password) e **SPID** (non automatizzabile, stesso discorso già fatto per
Inrete e Areti produttori). La cattura copre solo il percorso Form.

**Scoperta chiave della cattura**: il login non avviene su
`epuf.devalspa.com` ma viene delegato a un **servizio di autenticazione
centralizzato su un sottodominio separato**, `auth.devalspa.com`
(app "EIPAuth", verosimilmente condivisa da altri portali Terranova dello
stesso gruppo/vendor — da verificare se in futuro si guarda a un altro
distributore che usa lo stesso prodotto). La pagina `Login.tws` è solo la
"shell" visiva; il submit del form reindirizza/POSTa verso questo
servizio esterno.

Flusso completo osservato (2 POST, entrambi verso lo stesso URL):

1. `POST https://auth.devalspa.com/EIPAuth/auth_acc.m?app=PUF&mde=AUTH&lng=it-IT&dev=<uuid-dispositivo>&ip=<blob-opaco>&typ=login&prt=f&ret=<ReturnUrl-encoded>`
   — postback ASP.NET UpdatePanel standard (header `X-MicrosoftAjax:
   Delta=true`, `X-Requested-With: XMLHttpRequest`), con i campi
   `ctl06$cLoginAccess$txtUser` e `ctl06$cLoginAccess$txtPassword` oltre
   ai soliti `__VIEWSTATE`/`__EVENTVALIDATION`/`__EVENTTARGET
   =ctl06$cLoginAccess$btnLogin`. La risposta (formato "delta" ASP.NET
   AJAX, `1|#||4|<len>|updatePanel|ctl06_upBody|<html>`) sostituisce il
   pannello con un secondo form: **"Inserisci codice di verifica"** — 2FA
   **sempre richiesto** per il login a credenziali, confermando quanto
   diceva il manuale. Non un'ipotesi: osservato su un login reale.
2. `POST` allo stesso URL, con `ctl06$cTwoFactorsAuthentication$txtCode`
   (il codice a 6 cifre dell'app authenticator) e
   `__EVENTTARGET=ctl06$cTwoFactorsAuthentication$btnConfirm` (più un
   nuovo `__VIEWSTATE`, quello restituito dal passo 1 — la catena
   viewstate va rispettata tra i due submit). La risposta "delta" include
   uno `scriptStartupBlock` con
   `top.window.location.href='https://epuf.devalspa.com/EPUF/EPUF/EPUF/it-IT/DJY/Page/Login.tws?ReturnUrl=...&auth=<JWT>'`
   — il browser esegue questo redirect via JS.

Il **JWT** passato come `auth=` è firmato HS256 e contiene solo claim di
handoff, non un token di sessione applicativo:
```json
{"sid": "<id-sessione-auth>", "unique_name": "<username>", "syst": "Form",
 "locale": "it-IT", "nonce": "<nonce>", "iss": "PUF", "aud": "AUTH"}
```
Quel `GET Login.tws?...&auth=<JWT>` risponde **302** verso `Single.tws`
(nessun parametro `auth` nell'URL di destinazione) — da lì in poi la
sessione deve essere portata avanti via cookie.

> [!WARNING]
> La cattura è stata fatta con l'export HAR di **Safari/WebKit**
> (`creator.name: "WebInspector"`, nonostante lo User-Agent nelle
> richieste dichiari Chrome): **nessuna delle 71 richieste ha un header
> `Cookie` o `Set-Cookie`**, sullo stesso pattern già visto per
> [SET Distribuzione](set-distribuzione-protocol.md#login--verificato-con-cattura-reale-13092026).
> Non sappiamo quindi se dopo il redirect a `Single.tws` la sessione sia
> un cookie ASP.NET standard, un cookie custom, o qualcos'altro. Prossima
> cattura da fare con **Chrome o Firefox DevTools**.

Confermata anche la struttura del menu post-login (coerente col manuale):
la voce **Utenze** è il primo link, raggiunto via
`__doPostBack('ctl00$body$ctl00$mMenu1$FirstLevelMenuRepeater$ctl01$lnkLevelMenu','')`
— stesso meccanismo `__doPostBack` per tutte le voci di menu
(Sportello On-Line Elettrico, Comunicazioni utenti MT, TICA, ecc.), tutte
dirette a `POST .../Single.tws`.

**Registrazione**: self-service ma non istantanea come il Client
ID/Secret ID del protocollo PCF — il modulo (visto nel manuale) richiede
dati anagrafici completi, codice fiscale, **codice POD/PDR**, upload di
documenti richiesti e superamento di un **CAPTCHA**. Non blocca
l'automazione del login una volta registrati, ma significa che non è
possibile ottenere un account di prova "usa e getta" senza una fornitura
reale da collegare.

> [!NOTE]
> Il 2FA TOTP resta un ostacolo di automazione nuovo rispetto agli altri
> distributori già supportati (E-Distribuzione e Areti usano OTP via
> SMS/email one-shot, non un'app authenticator): oltre a identificativo e
> password servirebbe il *seed* condiviso dell'app authenticator, che
> l'utente dovrebbe recuperare lui stesso al momento della configurazione
> (di solito visibile come testo alternativo al QR code).

## Dati esposti — noti dal manuale, MAI verificati con una risposta reale

Il manuale (§2.1 "Utenze", pag. 8-11) descrive, per ogni POD/PDR
intestato, tre sotto-sezioni oltre al dettaglio anagrafico: **Pratiche**,
**Letture**, **Curve**.

| Sezione | Cosa mostra (dal manuale) |
|---|---|
| **Letture** | Storico letture (screenshot: dati disponibili dal 2018 al 2023 per l'account d'esempio) — per ogni lettura: Data, Misuratore, Attiva/Reattiva Fascia 1/2/3 (kWh/kVARh), Picco Fascia 1/2/3 (kW), Tipo lettura. Granularità **mensile** (una riga per mese), stesso tipo di dato delle letture PCF Duereti/Unareti. |
| **Curve** | "Nella sezione Curve è possibile scaricare le curve **per i POD trattati orari**" — griglia per Anno → mese, separata per Energia Attiva ed Energia Reattiva. **Non è documentato** il formato di export (CSV/XML/Excel), né se il click su una cella scarichi subito un file o apra un flusso a richiesta/ticket come il protocollo PCF. Lo screenshot d'esempio mostra la griglia **vuota** ("Nessun risultato" su tutti i mesi) — l'account usato per il manuale non ha (o non aveva, all'epoca dello screenshot) un POD trattato a orari. |

> [!NOTE]
> "Trattati orari" nel gergo del settore indica i POD con misuratore
> telegestito che il distributore tratta a granularità oraria (curva di
> carico), in contrapposizione ai POD "non trattati" che restano a dato
> mensile. Non tutte le utenze BT residenziali sono necessariamente
> "trattate orari" — va verificato caso per caso con un account reale.

Rispetto al punto di partenza di Edyna (cattura senza POD, zero pagine di
dati viste) questo è comunque un passo avanti: sappiamo *che tipo* di
dato esiste e come si chiamano le sezioni, e ora sappiamo anche *come* ci
si arriva (postback `__doPostBack` su `Single.tws`, vedi sopra) — manca
solo la risposta con dati reali dentro.

## Cosa manca

1. Un account **con almeno un POD associato** (idealmente "trattato
   orari", per vedere se la sezione Curve produce davvero un export).
2. Il *seed* TOTP condiviso dell'app authenticator per l'account di test
   (di solito visibile come testo accanto al QR code in fase di
   configurazione) — il 2FA è confermato **sempre obbligatorio** sul
   percorso Form (vedi sopra), quindi senza seed va rifatto login a mano
   ad ogni cattura; l'alternativa SPID resta non automatizzabile.
3. Una cattura HAR completa fatta con **Chrome o Firefox** DevTools
   (Network → Preserve log attivo *prima* di navigare → "Export HAR with
   content"), non Safari — per vedere l'header `Cookie`/`Set-Cookie` che
   la cattura attuale non mostra (vedi warning sopra) e per navigare
   fino a Utenze → Letture/Curve con un account che abbia dati da
   mostrare.
4. Col punto 3: capire se l'export delle Curve produce un file scaricabile
   diretto o un flusso differito (ticket), e in che formato.

## Come contribuire

Serve chi ha una fornitura Deval con un'utenza registrata sul PUF (POD
associato, idealmente con misuratore trattato a orari). Cattura HAR
manuale con **Chrome o Firefox** (non Safari, vedi warning sopra): login
+ sezione Letture + sezione Curve con almeno un mese che produca un
risultato. Vedi
[Aiutare senza scrivere codice](../CONTRIBUTING.md#aiutare-senza-scrivere-codice-raccolta-dati)
in CONTRIBUTING.md: **non allegare la HAR grezza a una issue pubblica**
(contiene token di sessione e dati personali in chiaro) — apri prima una
issue per concordare come condividerla.
