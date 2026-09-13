# Inrete Distribuzione — nessun accesso self-service per clienti finali BT

**Inrete Distribuzione Energia S.p.A.** (gruppo **Hera**, distribuisce
elettricità e gas in **Emilia-Romagna e Toscana**) — sede a Bologna.

Stato: **non implementabile per un cliente finale in bassa tensione**
(l'utenza domestica tipica a cui serve questa integrazione). Non è una
ricerca sospesa per mancanza di dati come [Edyna](edyna-protocol.md): qui
è il distributore stesso a dire esplicitamente, sulla propria pagina
pubblica, che i clienti finali in prelievo non hanno un portale proprio.

Fonte: pagina pubblica
<https://www.inretedistribuzione.it/energia-elettrica/clienti-finali-e-tecnici/misure-periodiche-mensili>
(letta 13/09/2026) — nessuna cattura HAR, non serve: non c'è un portale
da catturare per il caso che interessa.

## Verdetto

| | |
|---|---|
| Cliente finale BT (utenza domestica) ha un portale distributore self-service? | **No** — rimandato esplicitamente al **Portale Consumi ARERA** (`consumienergia.it/portaleConsumi`, solo SPID/CIE, non automatizzabile) o al proprio **venditore** |
| Esiste comunque un "Portale Distributore" Inrete? | Sì, `portale.inretedistribuzione.it`, ma per **utenti MT** (media tensione, energia reattiva immessa, Delibera 232/2022/R/EEL) e per **casi tecnici specifici** — non per il consumo BT residenziale |
| Come si ottengono le credenziali del Portale Distributore | **PEC** a `accesso.portalemisure@pec.inretedistribuzione.it` con modulo di accesso (+ modulo delega se applicabile) — processo manuale/burocratico, non un self-service Client ID/Secret ID come il protocollo PCF |
| Protocollo PCF (Duereti/Unareti)? | **Nessuna menzione** — non risulta che Inrete usi lo stesso schema |
| Produttori da fonti rinnovabili | Passano dal **GSE** (`auth.gse.it`), non da Inrete — fuori scope (l'integrazione oggi copre prelievo, non immissione) |

## Quadro generale

La pagina distingue quattro categorie, con canali diversi per ciascuna:

1. **Clienti finali in solo prelievo** (il caso comune, utenze
   domestiche/PMI senza produzione): nessun portale Inrete — rimandati al
   Portale Consumi ARERA o al venditore.
2. **Utenti MT**: portale Inrete per l'energia reattiva immessa in rete
   (obbligo normativo specifico, non consumo generico).
3. **Produttori da fonti rinnovabili**: GSE, non Inrete.
4. **Utenti generici con esigenze non coperte dagli altri canali**:
   possono comunque richiedere accesso al Portale Distributore via PEC.

Per un'integrazione Home Assistant orientata al cliente residenziale
tipico (categoria 1), non c'è nulla da automatizzare lato Inrete: è lo
stesso vicolo cieco già descritto in fondo a
[`edyna-protocol.md`](edyna-protocol.md) — la misura del cliente finale
passa dal **SII** (qui: Portale Consumi ARERA), non dal portale del
distributore, e il Portale Consumi accetta solo SPID/CIE, non
automatizzabile da Home Assistant.

> [!NOTE]
> Non è escluso che un cliente finale BT possa comunque ottenere accesso
> al Portale Distributore rientrando nella categoria 4 ("esigenze non
> coperte") — ma il processo è una richiesta manuale via PEC con modulo,
> non una registrazione self-service, e la pagina non lascia intendere
> che sia la via prevista per il consumo residenziale ordinario. Da
> verificare solo se qualcuno con una fornitura Inrete tenta comunque la
> richiesta e riesce a ottenere credenziali.

## Cosa manca (per riaprire la ricerca)

1. Conferma che un cliente finale BT ordinario **non** possa comunque
   registrarsi al Portale Distributore (oggi è un'inferenza dalla pagina
   pubblica, non un tentativo fatto).
2. Se qualcuno riuscisse ad accedere al Portale Distributore: una cattura
   HAR di login + pagina misure, per capire se espone dati di misura
   utili (curva di carico) o solo l'energia reattiva MT descritta nella
   pagina pubblica.
3. In assenza di quanto sopra, non c'è altro da fare: rientra nello
   stesso caso B, ma senza nemmeno un portale accessibile da catturare —
   più chiuso del caso Edyna.

## Come contribuire

Serve chi ha una fornitura Inrete e riesce a ottenere credenziali per
`portale.inretedistribuzione.it` (via la richiesta PEC descritta sopra).
Senza questo, non c'è cattura possibile: vedi
[Aiutare senza scrivere codice](../CONTRIBUTING.md#aiutare-senza-scrivere-codice-raccolta-dati)
in CONTRIBUTING.md per come condividere in sicurezza un'eventuale HAR
(mai allegarla grezza a una issue pubblica).
