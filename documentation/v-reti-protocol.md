# V-Reti — stato della ricerca (supporto non ancora implementato)

**V-Reti S.p.A.** (ex Megareti, nata dall'integrazione con Servizi a Rete —
gruppo **AGSM AIM**) — distributore di energia elettrica (e gas) nei
Comuni di **Verona**, **Vicenza** e **Grezzana**. P.IVA `03178060236`
(fonte: registro imprese). **Non ancora supportato.**

Segnalato dall'utente (14/09/2026) con un solo indizio: l'URL della
pagina di login,
`https://snc.v-reti.it/EPUF/PUF/it-IT/Default/Page/Login.tws?ReturnUrl=%2fEPUF%2fPUF%2fit-IT%2fDefault%2fPage%2fSingle.tws`.
Nessuna cattura HAR ancora disponibile: quanto segue è dedotto dalla sola
forma dell'URL, confrontata con quanto già verificato per
[Edyna](edyna-protocol.md) e [Deval](deval-protocol.md).

## Quadro generale

> [!NOTE]
> L'URL da solo basta a riconoscere lo stesso **prodotto Terranova**
> ("PUF" / "EPUF" / "EIPPUF", vedi la nota in
> [deval-protocol.md](deval-protocol.md#quadro-generale)) già confermato
> per Edyna e Deval: stessa estensione pagina `.tws`, stesso schema di
> path `/EPUF/<app>/it-IT/<token>/Page/*.tws`, stesso endpoint unico di
> navigazione `Single.tws` passato come `ReturnUrl`. Qui l'app si chiama
> `PUF` (invece di `EPUF` per Deval o `EIPPUF` per Edyna) e il token di
> sessione nel path è `Default` invece di un token opaco tipo `DJY`/`dD4`
> — probabilmente perché non è ancora stata avviata una sessione reale
> (la pagina di login "pre-auth" potrebbe usare sempre un placeholder
> `Default` finché non si compila il form). Da verificare con una cattura
> vera.
>
> Una ricerca web conferma inoltre che lo stesso PUF (stesso schema di
> URL e stesso manuale di registrazione, PDF quasi identico a quello di
> Deval) è usato anche da altri distributori gas non ancora rilevanti per
> questo repository (Apretigas Nord, Reti di Voghera): è un prodotto
> Terranova riusato da diversi distributori locali indipendenti, non solo
> dal gruppo Alperia (Edyna) e da Deval — un altro sblocco su una di
> queste istanze vale probabilmente anche per le altre.

Il sito pubblico di V-Reti espone una pagina dedicata,
[`v-reti.it/portale-utente`](https://www.v-reti.it/portale-utente), che
presenta il PUF come accesso per consultare lo stato delle proprie
utenze e le letture, oltre a pratiche di voltura/cessazione. Non ancora
verificato se il form di login sia solo a credenziali o preveda anche
SPID (come Deval) o un 2FA obbligatorio.

## Cosa manca

Praticamente tutto — a differenza di Edyna e Deval non c'è ancora
**nessuna** cattura, nemmeno di un account senza POD:

1. Una cattura HAR del **login** (Chrome o Firefox DevTools, Network →
   Preserve log attivo *prima* di navigare → "Export HAR with content"),
   anche solo con un account di prova, per confermare se il login
   delega a un servizio esterno come `auth.devalspa.com` per Deval, se
   richiede 2FA, e come viene mantenuta la sessione dopo il redirect.
2. Un account **con almeno un POD elettrico associato**, per arrivare
   alle sezioni Letture/Curve (per analogia con Deval, probabilmente
   sotto una voce di menu "Utenze").
3. Con quella cattura: la stessa verifica già fatta per Deval, ovvero se
   l'export delle curve produce un file scaricabile diretto o un flusso
   differito, e in che formato.

## Come contribuire

Serve chi ha una fornitura elettrica V-Reti (Verona, Vicenza o
Grezzana) con un'utenza registrata sul Portale Utente. Cattura HAR
manuale con **Chrome o Firefox** (non Safari — vedi il warning in
[deval-protocol.md](deval-protocol.md#login--verificato-con-cattura-reale-14092026)
sul perché Safari non va bene): login + sezione Utenze/Letture/Curve
con almeno un mese che produca un risultato. Vedi
[Aiutare senza scrivere codice](../CONTRIBUTING.md#aiutare-senza-scrivere-codice-raccolta-dati)
in CONTRIBUTING.md: **non allegare la HAR grezza a una issue pubblica**
(contiene token di sessione e dati personali in chiaro) — apri prima una
issue per concordare come condividerla.
