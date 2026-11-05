# Terranova RETIENERGIA / PUF — distributori che usano lo stesso portale

Edyna, Deval, V-Reti e DEA hanno tutti lo stesso portale clienti
(ASP.NET WebForms, pagine `.tws`, path `/EPUF/<app>/it-IT/<token>/Page/`,
navigazione via postback su `Single.tws`): è il prodotto **RETIENERGIA**
di **Terranova** (terranovasoftware.eu), di cui il PUF (Portale Utente
Finale) è il modulo self-service. Se si implementa il supporto per uno,
login e navigazione dovrebbero valere per tutti, cambiando solo host,
nome app e token.

## Clienti dichiarati da Terranova

Fonte: sezione "Hanno scelto RETIENERGIA" del sito Terranova
(screenshot del 04/10/2026). Che un distributore usi RETIENERGIA non
garantisce che esponga anche il PUF ai clienti finali: va verificato
caso per caso.

| Distributore | Zona | Stato nel repository |
|---|---|---|
| AMET S.p.A. (distribuzione: ADistribuzione Reti Trani) | Trani (BT) | nessun PUF trovato: solo un "Portale Utenti MT" (media tensione) |
| ASM Terni S.p.A. (TDE — Terni Distribuzione Elettrica) | Terni | portale `distribuzione.asmtde.it` (pagine `.aspx` con `idn=00AE`, stesso parametro `idn` del PUF): letture solo per utenti MT, curve orarie su richiesta via PEC — nessun PUF per BT trovato |
| DEA — Distribuzione Elettrica Adriatica | Osimo/Recanati, Ortona, Sanremo, Bresciano | [dea-protocol.md](dea-protocol.md) — login ok, nessun POD |
| Deval | Valle d'Aosta | [deval-protocol.md](deval-protocol.md) — login con 2FA, nessun POD |
| Edyna | Alto Adige | [edyna-protocol.md](edyna-protocol.md) — **supportato** (`distributors/edyna/`): prima cattura con POD (05/11/2026), curve orarie e al quarto d'ora; login verificato (nome utente, senza 2FA); catena Utenze → Curve → anno → mese verificata su una cattura reale (portale lentissimo, prima risposta delle curve vuota), non ancora provata in HA |
| Odoardo Zecca S.r.l. | Ortona (CH) | ramo distribuzione **ceduto a DEA nel 2023**: i suoi clienti stanno ora sul portale DEA |
| RetiPiù (gruppo A2A) | elettricità: solo Seregno (MB), ~26.000 utenze; gas in molti comuni della Brianza | **nessun PUF Terranova**: l'area clienti è `areaclienti.retipiu.it/ClientiRPiuWeb`, stessa piattaforma "Portale Clienti Finali" di Unareti (`/ClientiWeb`) e Duereti (`/ClientiDueRetiWeb`) — **supportato** via `pcf_common` (`distributors/retipiu.py`), vedi sotto |
| SIEC — Società per l'Illuminazione Elettrica in Chiavenna | Chiavenna e Prata Camportaccio (SO), ~8.000 utenze | portale clienti non trovato |
| V-Reti | Verona, Vicenza, Grezzana | [v-reti-protocol.md](v-reti-protocol.md) — login ok, account in attesa di validazione |

In pratica, dei cinque nomi nuovi nessuno aggiunge per ora un PUF per
clienti domestici: Zecca è confluita in DEA, RetiPiù usa un'altra
piattaforma, AMET e ASM Terni espongono solo servizi per la media
tensione, SIEC non ha un portale noto.

### RetiPiù e il protocollo PCF

Confermato dal manuale API ufficiale di RetiPiù (stesso percorso
`downloadFileTemplate.action?codice=MANUALE_RICH_API` di Duereti):
`HOST_NAME = areaclienti.retipiu.it/ClientiRPiuWeb`, stesse chiamate
`requestToken`/`requestExport`/`requestResult` di Duereti/Unareti. Aggiunto
come modulo sottile `distributors/retipiu.py` — vedi
[pcf-protocol.md](pcf-protocol.md). Non ancora provato con credenziali
reali.

## Istanze PUF confermate

| Distributore | URL di login | App | Token | 2FA |
|---|---|---|---|---|
| Edyna | vedi [edyna-protocol.md](edyna-protocol.md) | `EIPPUF` | `dD4` | no |
| Deval | `epuf.devalspa.com/EPUF/EPUF/it-IT/DJY/Page/Login.tws` | `EPUF` | `DJY` | sì (sempre, via `auth.devalspa.com`) |
| V-Reti | `snc.v-reti.it/EPUF/PUF/it-IT/D1E/Page/Login.tws` | `PUF` | `D1E` | no |
| DEA | `portale.deaelettrica.it/EPUF/PROD/it-IT/DG4/Page/Login.tws` | `PROD` | `DG4` | no |

Per DEA il token è risultato **identico in due sessioni distinte**, e
compare anche nei path del tema (`/EPUF/Frontend/customs/DG4/`): più che
un token di sessione sembra un identificativo fisso dell'istanza. Vale
probabilmente anche per gli altri, da ricontrollare.

## Cosa serve per sbloccarli tutti

Una prima cattura con un POD elettrico associato è arrivata da Edyna
(05/11/2026, [edyna-protocol.md](edyna-protocol.md)): Utenze → Curve →
Curve orarie mostra le curve orarie e al quarto d'ora in tabelle HTML.
La struttura (griglia Utenze con le azioni Dettaglio/Letture/Curve per
riga, tab dinamici, `Single.tws` come unico endpoint) è la stessa delle
altre istanze, quindi con buona probabilità vale per tutte, ma resta da
confermare. Per Edyna il login è confermato (nome utente e password, senza 2FA,
identico a V-Reti e DEA). Manca ancora la catena di postback con un POD
(Utenze → Curve → mese) e il comportamento dei pulsanti **Letture** e
degli export Excel.
