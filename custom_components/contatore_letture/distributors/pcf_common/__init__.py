"""Libreria condivisa per i distributori "PCF" (Duereti, Unareti).

Contiene la logica realmente identica tra i due (client API, coordinator,
import statistiche, sensori diagnostici, validazione config flow),
parametrizzata su base_url/display_name. Ogni distributore concreto vive
in un modulo separato (distributors/duereti.py, distributors/unareti.py)
che imposta questi parametri: se domani un distributore diverge, si tocca
solo il suo modulo, non questa libreria.
"""
