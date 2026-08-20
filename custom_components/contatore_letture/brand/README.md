# Icone e logo dell'integrazione

I file in questa cartella fanno comparire il logo di contatore_letture in
HACS e nell'interfaccia di Home Assistant. Funzionano da **Home Assistant
2026.3** in poi, che serve queste immagini direttamente dall'integrazione;
su versioni precedenti l'integrazione funziona ugualmente, solo senza icona
personalizzata.

## Icona attuale

Illustrazione originale (contatore elettrico stilizzato: display LCD a
sei cifre, simbolo di fulmine, indicatore di connettività, morsetti),
disegnata per questo progetto — nessun asset di terze parti, quindi nessun
vincolo di licenza sulla ridistribuzione.

`icon_source.svg` è la sorgente vettoriale: modificarla e rigenerare i PNG
è più semplice che editare i PNG direttamente. Per rigenerare:

```bash
pip install cairosvg
python3 -c "
import cairosvg
cairosvg.svg2png(url='icon_source.svg', write_to='icon.png', output_width=256, output_height=256)
cairosvg.svg2png(url='icon_source.svg', write_to='icon@2x.png', output_width=512, output_height=512)
"
```

## File richiesti

| File | Uso | Requisiti |
|---|---|---|
| `icon.png` | Icona quadrata, quella che vedi nell'elenco integrazioni | 1:1, almeno 128px, non oltre 256px |
| `icon@2x.png` | Versione hDPI dell'icona | 1:1, almeno 256px, non oltre 512px |
| `logo.png` | Logo esteso (opzionale, mostrato in alcune schermate) | formato landscape, rispetta le proporzioni del logo reale |
| `logo@2x.png` | Versione hDPI del logo (opzionale) | come sopra, doppia risoluzione |

## Requisiti tecnici comuni

- Formato **PNG**
- Sfondo trasparente
- Nessun testo scritto a mano sopra il logo, nessuna modifica rispetto all'originale
- Non usare loghi/immagini di Home Assistant, né dei singoli distributori
  (Duereti/Unareti/E-Distribuzione), per non generare confusione con
  un'integrazione ufficiale di uno di loro: contatore_letture è un progetto
  indipendente che si appoggia alle loro API pubbliche.

## Nota su home-assistant/brands (repository esterna)

Da **Home Assistant 2026.3** in poi, il repository ufficiale
[home-assistant/brands](https://github.com/home-assistant/brands) **non
accetta più Pull Request per integrazioni custom** — le chiude
automaticamente e rimanda a questo stesso meccanismo (`brand/` dentro
l'integrazione), servito da HA tramite un proxy locale
(`/api/brands/integration/{domain}/{immagine}`). Vedi l'annuncio ufficiale:
https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api

Quindi non serve (e non è più possibile) aprire una PR a `home-assistant/brands`
per questa integrazione: le immagini in questa cartella sono già tutto
quello che serve.

**Nota su HACS**: al momento (2026-08) la dashboard di HACS mostra
ancora "icona non disponibile" per le integrazioni che forniscono solo
icone locali come questa, perché il suo frontend punta ancora alla vecchia
CDN pubblica invece di usare il proxy locale (bug segnalato upstream:
[hacs/integration#5171](https://github.com/hacs/integration/issues/5171),
[#5223](https://github.com/hacs/integration/issues/5223)). L'icona
funziona comunque correttamente nella UI di Home Assistant stessa
(pagina Integrazioni, dispositivi, ecc.) — è solo la lista di HACS a non
mostrarla finché quel bug non viene risolto lato loro.
