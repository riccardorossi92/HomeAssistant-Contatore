# Deval — stato della ricerca (supporto non ancora implementato)

**Deval S.p.A. a s.u.** (distributore di energia elettrica in **Valle
d'Aosta**, sede ad Aosta) — portale **PUF (Portale Utente Finale)** su
`epuf.devalspa.com/EPUF`. **Non ancora supportato.**

P.IVA (da footer di [devalspa.it](https://www.devalspa.it)) = `IT 01013210073`.

A differenza di [Edyna](edyna-protocol.md), [Ireti](ireti-protocol.md) e
[SET Distribuzione](set-distribuzione-protocol.md), qui non è stata fatta
alcuna cattura HAR (nessun account disponibile, nemmeno senza POD): tutto
quello che segue viene da fonti **pubbliche e non autenticate** — il sito
[devalspa.it](https://www.devalspa.it), la pagina di login (raggiunta ma
non superata) e il **manuale utente ufficiale del PUF** (PDF pubblico,
scaricabile senza login dal link "Manuale del PUF" in fondo alla pagina
`/login`, letto 13/09/2026). Nonostante l'assenza di cattura, il manuale
da solo conferma più cose di quante se ne sapessero su Edyna prima di
qualunque cattura — vedi sotto.

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
> postback e — plausibilmente — stessa forma di export dati.

Significa, come per Edyna, che **non c'è un bundle JS da leggere per
indovinare gli endpoint dati**: l'unico modo per sapere davvero come
risponde il portale è navigarci con un account vero e guardare le
richieste POST verso l'endpoint `Single.tws` pagina per pagina.

## Login — osservato (non superato)

Da `https://www.devalspa.it/login`, il pulsante "Accedi" del box "Accesso
portale PUF" punta a `https://epuf.devalspa.com/EPUF` (`target="_blanck"`,
apre una nuova scheda). La root `/EPUF` senza path reindirizza al sito
marketing `devalspa.it`; l'URL che funziona davvero è
`https://epuf.devalspa.com/EPUF/EPUF/it-IT/DJY/Page/Login.tws?ReturnUrl=...`.

La pagina di login espone due percorsi (confermati sia dalla UI live sia
dal manuale):

1. **Form** (identificativo + password) — secondo il manuale, il login a
   credenziali richiede anche un **codice di verifica a due fattori**
   generato da un'app (Google Authenticator o Microsoft Authenticator),
   configurata al momento della registrazione via QR code. **Nessun altro
   distributore già supportato da questa integrazione usa un 2FA TOTP**
   (E-Distribuzione e Areti usano OTP via SMS/email one-shot, non
   un'app authenticator) — è un ostacolo di automazione nuovo: oltre a
   identificativo e password servirebbe anche il *seed* TOTP per generare
   il codice via software, cosa che l'utente dovrebbe recuperare lui
   stesso al momento della configurazione dell'app (di solito visibile
   come testo alternativo al QR code).
2. **SPID** — non automatizzabile, stesso discorso già fatto per Inrete e
   Areti (produttori).

**Registrazione**: self-service ma non istantanea come il Client
ID/Secret ID del protocollo PCF — il modulo (visto nel manuale) richiede
dati anagrafici completi, codice fiscale, **codice POD/PDR**, upload di
documenti richiesti e superamento di un **CAPTCHA**. Non blocca
l'automazione del login una volta registrati, ma significa che non è
possibile ottenere un account di prova "usa e getta" senza una fornitura
reale da collegare.

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
dato esiste e come si chiamano le sezioni, solo non sappiamo *come* si
ottiene via HTTP (niente bundle JS da leggere, a differenza di Ireti/SET).

## Cosa manca

1. Un account **con almeno un POD associato** (idealmente "trattato
   orari", per vedere se la sezione Curve produce davvero un export).
2. Se il login a credenziali richiede il 2FA TOTP come descritto nel
   manuale: il seed condiviso dell'app authenticator (di solito visibile
   come testo accanto al QR code in fase di configurazione), altrimenti
   l'unica via resta SPID (non automatizzabile) — da verificare comunque
   se il 2FA sia sempre obbligatorio o solo opzionale/condizionale.
3. Una cattura HAR completa (login + Letture + Curve, con un mese che
   restituisca dati) fatta con **Chrome o Firefox** DevTools (Network →
   Preserve log attivo *prima* di navigare → "Export HAR with content"),
   per leggere le richieste POST verso `Single.tws` pagina per pagina:
   parametri del postback e cosa cambia nella risposta.
4. Col punto 3: capire se l'export delle Curve produce un file scaricabile
   diretto o un flusso differito (ticket), e in che formato.

## Come contribuire

Serve chi ha una fornitura Deval con un'utenza registrata sul PUF (POD
associato, idealmente con misuratore trattato a orari). Cattura HAR
manuale: login (annotando se richiesto il 2FA) + sezione Letture + sezione
Curve con almeno un mese che produca un risultato. Vedi
[Aiutare senza scrivere codice](../CONTRIBUTING.md#aiutare-senza-scrivere-codice-raccolta-dati)
in CONTRIBUTING.md: **non allegare la HAR grezza a una issue pubblica**
(contiene token di sessione e dati personali in chiaro) — apri prima una
issue per concordare come condividerla.
