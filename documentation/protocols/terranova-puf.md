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
| AMET S.p.A. | Trani (BT) | non ancora esaminato |
| ASM Terni S.p.A. | Terni | non ancora esaminato |
| DEA — Distribuzione Elettrica Adriatica | Osimo/Recanati, Ortona, Sanremo, Bresciano | [dea-protocol.md](dea-protocol.md) — login ok, nessun POD |
| Deval | Valle d'Aosta | [deval-protocol.md](deval-protocol.md) — login con 2FA, nessun POD |
| Edyna | Alto Adige | [edyna-protocol.md](edyna-protocol.md) — login ok, nessun POD |
| Zecca ("energia vicina dal 1905") | da verificare | non ancora esaminato |
| RetiPiù | Brianza (gruppo AEB), da verificare | non ancora esaminato |
| SIEC soc. coop. | da verificare | non ancora esaminato |
| V-Reti | Verona, Vicenza, Grezzana | [v-reti-protocol.md](v-reti-protocol.md) — login ok, account in attesa di validazione |

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

Una sola cattura HAR da **un qualunque** distributore della lista con un
POD elettrico associato, che arrivi a Utenze → Letture/Curve: la pagina
Utenze di DEA mostra già, per ogni riga, le colonne azione Dettaglio,
Letture e Curve.
