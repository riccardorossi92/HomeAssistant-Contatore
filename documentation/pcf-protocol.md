# Duereti / Unareti — protocollo PCF

Codice: `custom_components/contatore_letture/distributors/pcf_common/`.
Protocollo "Portale Clienti Finali" (Client ID + Secret ID, abilitazione
manuale via portale), a due fasi: richiesta di un ticket di export
(`requestExport`), poi polling finché il ticket è pronto
(`requestResult`) e download del file (CSV per la curva, XLSX per le
letture periodiche).

`pcf_common/*` è condiviso tra Duereti e Unareti — vedi
`architecture.md` per come è stato derivato dal codice originale di
`duereti_letture`.

## Nota sulla documentazione del WAF/DST

Alcuni commenti tecnici in `pcf_common/api.py` e
`pcf_common/statistics.py` documentano comportamenti (blocchi del WAF,
interpretazione del flag ora legale nel CSV) **verificati sul campo solo
su Duereti**, non ancora riconfermati su Unareti. Sono segnalati
esplicitamente nei commenti: se in futuro emergono differenze su
Unareti, vanno annotate lì (non serve più toccare due file diversi con
la stessa logica duplicata, come accadeva prima dell'unificazione in
`pcf_common`).

In particolare:
- **Retry del WAF**: alcune risposte 403/429 sono blocchi temporanei del
  WAF del portale, non errori reali — gestiti con retry automatico.
- **Interpretazione del cambio ora legale**: il CSV della curva di
  carico Duereti porta un flag (`FL_ORA_LEGALE`) che va interpretato per
  calcolare correttamente il timestamp di ogni campione nei giorni di
  transizione — verificato su un export di 181 giorni comprendente una
  transizione reale. A differenza di E-Distribuzione (vedi
  `edistribuzione-protocol.md`), qui il timestamp non è già assoluto nel
  dato grezzo, va ricostruito interpretando il flag.
