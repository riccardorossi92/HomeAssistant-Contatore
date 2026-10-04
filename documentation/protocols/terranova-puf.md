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
| Edyna | Alto Adige | [edyna-protocol.md](edyna-protocol.md) — login ok, nessun POD |
| Odoardo Zecca S.r.l. | Ortona (CH) | ramo distribuzione **ceduto a DEA nel 2023**: i suoi clienti stanno ora sul portale DEA |
| RetiPiù | Desio/Seregno (Brianza) | **nessun PUF Terranova**: l'area clienti è `areaclienti.retipiu.it/ClientiRPiuWeb`, stessa piattaforma "Portale Clienti Finali" di Unareti (`/ClientiWeb`) e Duereti (`/ClientiDueRetiWeb`) — candidato per `pcf_common`, vedi sotto |
| SIEC — Società per l'Illuminazione Elettrica in Chiavenna | Chiavenna e Prata Camportaccio (SO), ~8.000 utenze | portale clienti non trovato |
| V-Reti | Verona, Vicenza, Grezzana | [v-reti-protocol.md](v-reti-protocol.md) — login ok, account in attesa di validazione |

In pratica, dei cinque nomi nuovi nessuno aggiunge per ora un PUF per
clienti domestici: Zecca è confluita in DEA, RetiPiù usa un'altra
piattaforma, AMET e ASM Terni espongono solo servizi per la media
tensione, SIEC non ha un portale noto.

### RetiPiù e il protocollo PCF

Il path `ClientiRPiuWeb` segue lo stesso schema di Unareti e Duereti, già
supportati da `pcf_common`, e RetiPiù pubblica un manuale intitolato
"[PCF - TICA] Manuale portale clienti finali". Se RetiPiù espone anche
le API PCF (abilitazione manuale, Client ID + Secret ID), il supporto
potrebbe ridursi a un modulo sottile come `unareti.py` con
`BASE_URL = "https://areaclienti.retipiu.it/ClientiRPiuWeb/public/misure"`.
**Non verificato**: serve un cliente RetiPiù che controlli se nella sua
area clienti compare la richiesta di abilitazione API.

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
