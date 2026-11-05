"""Pagine del portale Edyna per i test, anonimizzate.

I frammenti HTML sono il markup REALE della cattura del 05/11/2026 (account
con un POD attivo): griglia Utenze, selettore Anno, tabella dei mesi,
griglia delle curve orarie, intestazioni dei tab, voci di menu. POD, venditore,
matricola, indirizzo e tutti i consumi sono sostituiti da valori sintetici.
Le pagine intere (viewstate, form) sono ricostruite attorno ai frammenti: il
portale ne restituisce di più grandi (~250 KB), ma questi sono gli elementi
che il client legge.

Il modulo di login (GET Login.tws) NON è nelle catture: qui è un modulo
ricostruito con i nomi di campo del POST di login catturato.
"""
from __future__ import annotations

POD = "IT000E00000001"
POD_IMMISSIONE = "IT000E00000002"
POD_SECONDO = "IT000E00000003"

# Voci di menu di primo livello (Utenze, Comunicazioni).
MENU_UTENZE_E_COMUNICAZIONI = (
    '<a href="javascript:__doPostBack(\'ctl00$body$ctl00$mMenu1$FirstLevelMenuRepeater$ctl01$lnk'
    'LevelMenu\',\'\')" id="body_ctl00_mMenu1_FirstLevelMenuRepeater_lnkLevelMenu_0" onclick="Show'
    'Wait(\'#cWaiting_upWaiting\');">Utenze</a><a href="javascript:__doPostBack(\'ctl00$body$ctl00'
    '$mMenu1$FirstLevelMenuRepeater$ctl03$lnkLevelMenu\',\'\')" id="body_ctl00_mMenu1_FirstLevelMe'
    'nuRepeater_lnkLevelMenu_2" onclick="ShowWait(\'#cWaiting_upWaiting\');"><span class="fa fa-c'
    'hevron-down down_arrow"></span> Comunicazioni</a>'
)

# Campo di ricerca della pagina Utenze (segnala che la pagina è arrivata).
INPUT_RICERCA_UTENZE = (
    '<input class="form-control" id="body_ctl00_ctl00_tcListUtenze_TList_cUFListUtenze_txtCodut'
    'e" name="ctl00$body$ctl00$ctl00$tcListUtenze$TList$cUFListUtenze$txtCodute" style="width:1'
    '00%;" type="text"/>'
)

# Griglia Utenze con un POD elettrico di prelievo.
GRIGLIA_UTENZE = (
    '<table cellspacing="0" class="ps-gv" id="body_ctl00_ctl00_tcListUtenze_TList_cUFListUtenze'
    '_gvUtenze" style="border-collapse:collapse;"><tr><th scope="col"><span>Servizio</span></th'
    '><th scope="col"><span>Codice utenza</span></th><th scope="col"><span>Ruolo</span></th><th'
    ' scope="col"><span>Venditore</span></th><th scope="col"><span>Codice Misuratore</span></th'
    '><th scope="col"><span>Indirizzo</span></th><th scope="col"><span>Citta/Provincia</span></'
    'th><th scope="col"><span>Stato</span></th><th align="center" scope="col" style="width:60px'
    ';"><span>Dettaglio</span></th><th align="center" scope="col" style="width:60px;"><span>Pra'
    'tiche</span></th><th align="center" scope="col" style="width:60px;"><span>Letture</span></'
    'th><th align="center" scope="col" style="width:60px;"><span>Curve</span></th></tr><tr clas'
    's="ps-row-select"><td> Energia Elettrica </td><td> IT000E00000001 <i class="fa fa-flash te'
    'xt-warning" style="color: red;"></i></td><td>Prelievo</td><td>Venditore Esempio</td><td>MA'
    'TRICOLA0001</td><td>VIA ESEMPIO, 1</td><td>CITTA ESEMPIO - BZ</td><td> Attivo </td><td ali'
    'gn="center"><a href="javascript:__doPostBack(\'ctl00$body$ctl00$ctl00$tcListUtenze$TList$cU'
    'FListUtenze$gvUtenze$ctl02$btnDettaglio\',\'\')" id="body_ctl00_ctl00_tcListUtenze_TList_cUFL'
    'istUtenze_gvUtenze_btnDettaglio_0" onclick="ShowWait(\'#cWaiting_upWaiting\');"><i class="fa'
    ' fa-info-circle"></i></a></td><td align="center"><a href="javascript:__doPostBack(\'ctl00$b'
    'ody$ctl00$ctl00$tcListUtenze$TList$cUFListUtenze$gvUtenze$ctl02$btnMonitoraggio\',\'\')" id="'
    'body_ctl00_ctl00_tcListUtenze_TList_cUFListUtenze_gvUtenze_btnMonitoraggio_0" onclick="Sho'
    'wWait(\'#cWaiting_upWaiting\');"><i class="fa fa-info-circle"></i></a></td><td align="center'
    '"><a href="javascript:__doPostBack(\'ctl00$body$ctl00$ctl00$tcListUtenze$TList$cUFListUtenz'
    'e$gvUtenze$ctl02$btnLetture\',\'\')" id="body_ctl00_ctl00_tcListUtenze_TList_cUFListUtenze_gv'
    'Utenze_btnLetture_0" onclick="ShowWait(\'#cWaiting_upWaiting\');"><i class="fa fa-info-circl'
    'e"></i></a></td><td align="center"><a href="javascript:__doPostBack(\'ctl00$body$ctl00$ctl0'
    '0$tcListUtenze$TList$cUFListUtenze$gvUtenze$ctl02$btnCurve\',\'\')" id="body_ctl00_ctl00_tcLi'
    'stUtenze_TList_cUFListUtenze_gvUtenze_btnCurve_0" onclick="ShowWait(\'#cWaiting_upWaiting\')'
    ';"><i class="fa fa-info-circle"></i></a></td></tr></table>'
)

# Griglia Utenze con: prelievo, immissione, gas e un secondo prelievo.
GRIGLIA_UTENZE_MULTI = (
    '<table cellspacing="0" class="ps-gv" id="body_ctl00_ctl00_tcListUtenze_TList_cUFListUtenze'
    '_gvUtenze" style="border-collapse:collapse;"><tr><th scope="col"><span>Servizio</span></th'
    '><th scope="col"><span>Codice utenza</span></th><th scope="col"><span>Ruolo</span></th><th'
    ' scope="col"><span>Venditore</span></th><th scope="col"><span>Codice Misuratore</span></th'
    '><th scope="col"><span>Indirizzo</span></th><th scope="col"><span>Citta/Provincia</span></'
    'th><th scope="col"><span>Stato</span></th><th align="center" scope="col" style="width:60px'
    ';"><span>Dettaglio</span></th><th align="center" scope="col" style="width:60px;"><span>Pra'
    'tiche</span></th><th align="center" scope="col" style="width:60px;"><span>Letture</span></'
    'th><th align="center" scope="col" style="width:60px;"><span>Curve</span></th></tr><tr clas'
    's="ps-row-select"><td> Energia Elettrica </td><td> IT000E00000001 <i class="fa fa-flash te'
    'xt-warning" style="color: red;"></i></td><td>Prelievo</td><td>Venditore Esempio</td><td>MA'
    'TRICOLA0001</td><td>VIA ESEMPIO, 1</td><td>CITTA ESEMPIO - BZ</td><td> Attivo </td><td ali'
    'gn="center"><a href="javascript:__doPostBack(\'ctl00$body$ctl00$ctl00$tcListUtenze$TList$cU'
    'FListUtenze$gvUtenze$ctl02$btnDettaglio\',\'\')" id="body_ctl00_ctl00_tcListUtenze_TList_cUFL'
    'istUtenze_gvUtenze_btnDettaglio_0" onclick="ShowWait(\'#cWaiting_upWaiting\');"><i class="fa'
    ' fa-info-circle"></i></a></td><td align="center"><a href="javascript:__doPostBack(\'ctl00$b'
    'ody$ctl00$ctl00$tcListUtenze$TList$cUFListUtenze$gvUtenze$ctl02$btnMonitoraggio\',\'\')" id="'
    'body_ctl00_ctl00_tcListUtenze_TList_cUFListUtenze_gvUtenze_btnMonitoraggio_0" onclick="Sho'
    'wWait(\'#cWaiting_upWaiting\');"><i class="fa fa-info-circle"></i></a></td><td align="center'
    '"><a href="javascript:__doPostBack(\'ctl00$body$ctl00$ctl00$tcListUtenze$TList$cUFListUtenz'
    'e$gvUtenze$ctl02$btnLetture\',\'\')" id="body_ctl00_ctl00_tcListUtenze_TList_cUFListUtenze_gv'
    'Utenze_btnLetture_0" onclick="ShowWait(\'#cWaiting_upWaiting\');"><i class="fa fa-info-circl'
    'e"></i></a></td><td align="center"><a href="javascript:__doPostBack(\'ctl00$body$ctl00$ctl0'
    '0$tcListUtenze$TList$cUFListUtenze$gvUtenze$ctl02$btnCurve\',\'\')" id="body_ctl00_ctl00_tcLi'
    'stUtenze_TList_cUFListUtenze_gvUtenze_btnCurve_0" onclick="ShowWait(\'#cWaiting_upWaiting\')'
    ';"><i class="fa fa-info-circle"></i></a></td></tr><tr class="ps-row-select"><td>Energia El'
    'ettrica</td><td> IT000E00000002 <i class="fa fa-flash text-warning" style="color: red;"></'
    'i></td><td>Immissione</td><td>Venditore Esempio</td><td>MATRICOLA0001</td><td>VIA ESEMPIO,'
    ' 1</td><td>CITTA ESEMPIO - BZ</td><td> Attivo </td><td align="center"><a href="javascript:'
    "__doPostBack('ctl00$body$ctl00$ctl00$tcListUtenze$TList$cUFListUtenze$gvUtenze$ctl03$btnDe"
    'ttaglio\',\'\')" id="body_ctl00_ctl00_tcListUtenze_TList_cUFListUtenze_gvUtenze_btnDettaglio_'
    '1" onclick="ShowWait(\'#cWaiting_upWaiting\');"><i class="fa fa-info-circle"></i></a></td><t'
    'd align="center"><a href="javascript:__doPostBack(\'ctl00$body$ctl00$ctl00$tcListUtenze$TLi'
    'st$cUFListUtenze$gvUtenze$ctl03$btnMonitoraggio\',\'\')" id="body_ctl00_ctl00_tcListUtenze_TL'
    'ist_cUFListUtenze_gvUtenze_btnMonitoraggio_1" onclick="ShowWait(\'#cWaiting_upWaiting\');"><'
    'i class="fa fa-info-circle"></i></a></td><td align="center"><a href="javascript:__doPostBa'
    "ck('ctl00$body$ctl00$ctl00$tcListUtenze$TList$cUFListUtenze$gvUtenze$ctl03$btnLetture','')"
    '" id="body_ctl00_ctl00_tcListUtenze_TList_cUFListUtenze_gvUtenze_btnLetture_1" onclick="Sh'
    'owWait(\'#cWaiting_upWaiting\');"><i class="fa fa-info-circle"></i></a></td><td align="cente'
    'r"><a href="javascript:__doPostBack(\'ctl00$body$ctl00$ctl00$tcListUtenze$TList$cUFListUten'
    'ze$gvUtenze$ctl03$btnCurve\',\'\')" id="body_ctl00_ctl00_tcListUtenze_TList_cUFListUtenze_gvU'
    'tenze_btnCurve_1" onclick="ShowWait(\'#cWaiting_upWaiting\');"><i class="fa fa-info-circle">'
    '</i></a></td></tr><tr class="ps-row-select"><td>Gas</td><td> 00000000000001 <i class="fa f'
    'a-flash text-warning" style="color: red;"></i></td><td>Prelievo</td><td>Venditore Esempio<'
    '/td><td>MATRICOLA0001</td><td>VIA ESEMPIO, 1</td><td>CITTA ESEMPIO - BZ</td><td> Attivo </'
    'td><td align="center"><a href="javascript:__doPostBack(\'ctl00$body$ctl00$ctl00$tcListUtenz'
    'e$TList$cUFListUtenze$gvUtenze$ctl04$btnDettaglio\',\'\')" id="body_ctl00_ctl00_tcListUtenze_'
    'TList_cUFListUtenze_gvUtenze_btnDettaglio_2" onclick="ShowWait(\'#cWaiting_upWaiting\');"><i'
    ' class="fa fa-info-circle"></i></a></td><td align="center"><a href="javascript:__doPostBac'
    "k('ctl00$body$ctl00$ctl00$tcListUtenze$TList$cUFListUtenze$gvUtenze$ctl04$btnMonitoraggio'"
    ',\'\')" id="body_ctl00_ctl00_tcListUtenze_TList_cUFListUtenze_gvUtenze_btnMonitoraggio_2" on'
    'click="ShowWait(\'#cWaiting_upWaiting\');"><i class="fa fa-info-circle"></i></a></td><td ali'
    'gn="center"><a href="javascript:__doPostBack(\'ctl00$body$ctl00$ctl00$tcListUtenze$TList$cU'
    'FListUtenze$gvUtenze$ctl04$btnLetture\',\'\')" id="body_ctl00_ctl00_tcListUtenze_TList_cUFLis'
    'tUtenze_gvUtenze_btnLetture_2" onclick="ShowWait(\'#cWaiting_upWaiting\');"><i class="fa fa-'
    'info-circle"></i></a></td><td align="center"><a href="javascript:__doPostBack(\'ctl00$body$'
    'ctl00$ctl00$tcListUtenze$TList$cUFListUtenze$gvUtenze$ctl04$btnCurve\',\'\')" id="body_ctl00_'
    'ctl00_tcListUtenze_TList_cUFListUtenze_gvUtenze_btnCurve_2" onclick="ShowWait(\'#cWaiting_u'
    'pWaiting\');"><i class="fa fa-info-circle"></i></a></td></tr><tr class="ps-row-select"><td>'
    'Energia Elettrica</td><td> IT000E00000003 <i class="fa fa-flash text-warning" style="color'
    ': red;"></i></td><td>Prelievo</td><td>Venditore Esempio</td><td>MATRICOLA0001</td><td>VIA '
    'ESEMPIO, 1</td><td>CITTA ESEMPIO - BZ</td><td> Attivo </td><td align="center"><a href="jav'
    "ascript:__doPostBack('ctl00$body$ctl00$ctl00$tcListUtenze$TList$cUFListUtenze$gvUtenze$ctl"
    '05$btnDettaglio\',\'\')" id="body_ctl00_ctl00_tcListUtenze_TList_cUFListUtenze_gvUtenze_btnDe'
    'ttaglio_3" onclick="ShowWait(\'#cWaiting_upWaiting\');"><i class="fa fa-info-circle"></i></a'
    '></td><td align="center"><a href="javascript:__doPostBack(\'ctl00$body$ctl00$ctl00$tcListUt'
    'enze$TList$cUFListUtenze$gvUtenze$ctl05$btnMonitoraggio\',\'\')" id="body_ctl00_ctl00_tcListU'
    'tenze_TList_cUFListUtenze_gvUtenze_btnMonitoraggio_3" onclick="ShowWait(\'#cWaiting_upWaiti'
    'ng\');"><i class="fa fa-info-circle"></i></a></td><td align="center"><a href="javascript:__'
    "doPostBack('ctl00$body$ctl00$ctl00$tcListUtenze$TList$cUFListUtenze$gvUtenze$ctl05$btnLett"
    'ure\',\'\')" id="body_ctl00_ctl00_tcListUtenze_TList_cUFListUtenze_gvUtenze_btnLetture_3" onc'
    'lick="ShowWait(\'#cWaiting_upWaiting\');"><i class="fa fa-info-circle"></i></a></td><td alig'
    'n="center"><a href="javascript:__doPostBack(\'ctl00$body$ctl00$ctl00$tcListUtenze$TList$cUF'
    'ListUtenze$gvUtenze$ctl05$btnCurve\',\'\')" id="body_ctl00_ctl00_tcListUtenze_TList_cUFListUt'
    'enze_gvUtenze_btnCurve_3" onclick="ShowWait(\'#cWaiting_upWaiting\');"><i class="fa fa-info-'
    'circle"></i></a></td></tr></table>'
)

# Selettore Anno del tab Curve (2026 selezionato).
SELETTORE_ANNO = (
    '<select class="form-control ps-inline" id="body_ctl00_ctl00_tcListUtenze_TCurve_cCurve_ddl'
    'Anno" name="ctl00$body$ctl00$ctl00$tcListUtenze$TCurve$cCurve$ddlAnno" onchange="javascrip'
    "t:setTimeout('__doPostBack(\\'ctl00$body$ctl00$ctl00$tcListUtenze$TCurve$cCurve$ddlAnno\\',\\"
    '\'\\\')\', 0)" style="width:100px;"><option selected="selected" value="2026">2026</option><opt'
    'ion value="2025">2025</option><option value="2024">2024</option><option value="2023">2023<'
    '/option><option value="2022">2022</option><option value="2021">2021</option><option value='
    '"2020">2020</option></select>'
)

# Tabella dell'Energia Attiva: gennaio-ottobre con valore, novembre e dicembre vuoti.
TABELLA_MESI_VUOTA = (
    '<table cellspacing="0" class="ps-gv ps-gv-csm" id="body_ctl00_ctl00_tcListUtenze_TCurve_cC'
    'urve_gvCurveAttiva" style="border-collapse:collapse;"><tr><th scope="col"><span>Gennaio</s'
    'pan></th><th scope="col"><span>Febbraio</span></th><th scope="col"><span>Marzo</span></th>'
    '<th scope="col"><span>Aprile</span></th><th scope="col"><span>Maggio</span></th><th scope='
    '"col"><span>Giugno</span></th><th scope="col"><span>Luglio</span></th><th scope="col"><spa'
    'n>Agosto</span></th><th scope="col"><span>Settembre</span></th><th scope="col"><span>Ottob'
    're</span></th><th scope="col"><span>Novembre</span></th><th scope="col"><span>Dicembre</sp'
    'an></th></tr><tr><td colspan="12"><div class="alert alert-danger"> Nessun risultato </div>'
    "</td></tr></table>"
)  # come arriva alla prima richiesta di Curve o di un anno nuovo (cattura del 05/11/2026)

TABELLA_MESI = (
    '<table cellspacing="0" class="ps-gv ps-gv-csm" id="body_ctl00_ctl00_tcListUtenze_TCurve_cC'
    'urve_gvCurveAttiva" style="border-collapse:collapse;"><tr><th scope="col"><span>Gennaio</s'
    'pan></th><th scope="col"><span>Febbraio</span></th><th scope="col"><span>Marzo</span></th>'
    '<th scope="col"><span>Aprile</span></th><th scope="col"><span>Maggio</span></th><th scope='
    '"col"><span>Giugno</span></th><th scope="col"><span>Luglio</span></th><th scope="col"><spa'
    'n>Agosto</span></th><th scope="col"><span>Settembre</span></th><th scope="col"><span>Ottob'
    're</span></th><th scope="col"><span>Novembre</span></th><th scope="col"><span>Dicembre</sp'
    'an></th></tr><tr><td align="center"><a class="no_wrap" href="javascript:__doPostBack(\'ctl0'
    '0$body$ctl00$ctl00$tcListUtenze$TCurve$cCurve$gvCurveAttiva$ctl02$btnCurve1\',\'\')" id="body'
    '_ctl00_ctl00_tcListUtenze_TCurve_cCurve_gvCurveAttiva_btnCurve1_0">110,500 <i class="fa fa'
    '-info-circle"></i></a></td><td align="center"><a class="no_wrap" href="javascript:__doPost'
    "Back('ctl00$body$ctl00$ctl00$tcListUtenze$TCurve$cCurve$gvCurveAttiva$ctl02$btnCurve2','')"
    '" id="body_ctl00_ctl00_tcListUtenze_TCurve_cCurve_gvCurveAttiva_btnCurve2_0">120,500 <i cl'
    'ass="fa fa-info-circle"></i></a></td><td align="center"><a class="no_wrap" href="javascrip'
    "t:__doPostBack('ctl00$body$ctl00$ctl00$tcListUtenze$TCurve$cCurve$gvCurveAttiva$ctl02$btnC"
    'urve3\',\'\')" id="body_ctl00_ctl00_tcListUtenze_TCurve_cCurve_gvCurveAttiva_btnCurve3_0">130'
    ',500 <i class="fa fa-info-circle"></i></a></td><td align="center"><a class="no_wrap" href='
    '"javascript:__doPostBack(\'ctl00$body$ctl00$ctl00$tcListUtenze$TCurve$cCurve$gvCurveAttiva$'
    'ctl02$btnCurve4\',\'\')" id="body_ctl00_ctl00_tcListUtenze_TCurve_cCurve_gvCurveAttiva_btnCur'
    've4_0">140,500 <i class="fa fa-info-circle"></i></a></td><td align="center"><a class="no_w'
    'rap" href="javascript:__doPostBack(\'ctl00$body$ctl00$ctl00$tcListUtenze$TCurve$cCurve$gvCu'
    'rveAttiva$ctl02$btnCurve5\',\'\')" id="body_ctl00_ctl00_tcListUtenze_TCurve_cCurve_gvCurveAtt'
    'iva_btnCurve5_0">150,500 <i class="fa fa-info-circle"></i></a></td><td align="center"><a c'
    'lass="no_wrap" href="javascript:__doPostBack(\'ctl00$body$ctl00$ctl00$tcListUtenze$TCurve$c'
    'Curve$gvCurveAttiva$ctl02$btnCurve6\',\'\')" id="body_ctl00_ctl00_tcListUtenze_TCurve_cCurve_'
    'gvCurveAttiva_btnCurve6_0">160,500 <i class="fa fa-info-circle"></i></a></td><td align="ce'
    'nter"><a class="no_wrap" href="javascript:__doPostBack(\'ctl00$body$ctl00$ctl00$tcListUtenz'
    'e$TCurve$cCurve$gvCurveAttiva$ctl02$btnCurve7\',\'\')" id="body_ctl00_ctl00_tcListUtenze_TCur'
    've_cCurve_gvCurveAttiva_btnCurve7_0">170,500 <i class="fa fa-info-circle"></i></a></td><td'
    ' align="center"><a class="no_wrap" href="javascript:__doPostBack(\'ctl00$body$ctl00$ctl00$t'
    'cListUtenze$TCurve$cCurve$gvCurveAttiva$ctl02$btnCurve8\',\'\')" id="body_ctl00_ctl00_tcListU'
    'tenze_TCurve_cCurve_gvCurveAttiva_btnCurve8_0">180,500 <i class="fa fa-info-circle"></i></'
    'a></td><td align="center"><a class="no_wrap" href="javascript:__doPostBack(\'ctl00$body$ctl'
    '00$ctl00$tcListUtenze$TCurve$cCurve$gvCurveAttiva$ctl02$btnCurve9\',\'\')" id="body_ctl00_ctl'
    '00_tcListUtenze_TCurve_cCurve_gvCurveAttiva_btnCurve9_0">190,500 <i class="fa fa-info-circ'
    'le"></i></a></td><td align="center"><a class="no_wrap" href="javascript:__doPostBack(\'ctl0'
    '0$body$ctl00$ctl00$tcListUtenze$TCurve$cCurve$gvCurveAttiva$ctl02$btnCurve10\',\'\')" id="bod'
    'y_ctl00_ctl00_tcListUtenze_TCurve_cCurve_gvCurveAttiva_btnCurve10_0">200,500 <i class="fa '
    'fa-info-circle"></i></a></td><td align="center"><a class="no_wrap" href="javascript:__doPo'
    "stBack('ctl00$body$ctl00$ctl00$tcListUtenze$TCurve$cCurve$gvCurveAttiva$ctl02$btnCurve11',"
    '\'\')" id="body_ctl00_ctl00_tcListUtenze_TCurve_cCurve_gvCurveAttiva_btnCurve11_0"></a></td>'
    '<td align="center"><a class="no_wrap" href="javascript:__doPostBack(\'ctl00$body$ctl00$ctl0'
    '0$tcListUtenze$TCurve$cCurve$gvCurveAttiva$ctl02$btnCurve12\',\'\')" id="body_ctl00_ctl00_tcL'
    'istUtenze_TCurve_cCurve_gvCurveAttiva_btnCurve12_0"></a></td></tr></table>'
)

# Linguetta del tab Curve ('Dati di misura: POD').
INTESTAZIONE_TAB_CURVE = (
    '<span class="ajax__tab_tab" id="__tab_body_ctl00_ctl00_tcListUtenze_TCurve"> Dati di misur'
    'a: IT000E00000001 <a class="fa fa-close" href="javascript:__doPostBack(\'ctl00$body$ctl00$c'
    'tl00$tcListUtenze$TCurve$cHeaderTabCurveMesi$btnCloseTab\',\'\')" id="body_ctl00_ctl00_tcList'
    'Utenze_TCurve_cHeaderTabCurveMesi_btnCloseTab" style="font-size:15px;"></a></span>'
)

# Linguetta del tab Curve orarie ('Energia Attiva: POD').
INTESTAZIONE_TAB_CURVE_ORARIE = (
    '<span class="ajax__tab_tab" id="__tab_body_ctl00_ctl00_tcListUtenze_TCurveOrarie"> Energia'
    ' Attiva: IT000E00000001 <a class="fa fa-close" href="javascript:__doPostBack(\'ctl00$body$c'
    'tl00$ctl00$tcListUtenze$TCurveOrarie$cHeaderTabCurveOrarie$btnCloseTab\',\'\')" id="body_ctl0'
    '0_ctl00_tcListUtenze_TCurveOrarie_cHeaderTabCurveOrarie_btnCloseTab" style="font-size:15px'
    ';"></a></span>'
)

# Curve orarie: 01/10, 02/10, 24/10 (24 ore) e 25/10 (25 ore, fine ora legale).
GRIGLIA_ORARIA_OTTOBRE = (
    '<table class="ps-gv" cellspacing="0" id="body_ctl00_ctl00_tcListUtenze_TCurveOrarie_cCurve'
    'Orarie_gvCurveOrarieEE" style="border-collapse:collapse;overflow:auto;"><tr class="UftrRpt'
    'Head"><th scope="col"><span>Giorno</span></th><th scope="col"><span><p class="no_wrap">Ora'
    ' 1</p><p class="no_wrap">00 — 01</p></span></th><th scope="col"><span><p class="no_wrap">O'
    'ra 2</p><p class="no_wrap">01 — 02</p></span></th><th scope="col"><span><p class="no_wrap"'
    '>Ora 3</p><p class="no_wrap">02 — 03</p></span></th><th scope="col"><span><p class="no_wra'
    'p">Ora 4</p><p class="no_wrap">03 — 04</p></span></th><th scope="col"><span><p class="no_w'
    'rap">Ora 5</p><p class="no_wrap">04 — 05</p></span></th><th scope="col"><span><p class="no'
    '_wrap">Ora 6</p><p class="no_wrap">05 — 06</p></span></th><th scope="col"><span><p class="'
    'no_wrap">Ora 7</p><p class="no_wrap">06 — 07</p></span></th><th scope="col"><span><p class'
    '="no_wrap">Ora 8</p><p class="no_wrap">07 — 08</p></span></th><th scope="col"><span><p cla'
    'ss="no_wrap">Ora 9</p><p class="no_wrap">08 — 09</p></span></th><th scope="col"><span><p c'
    'lass="no_wrap">Ora 10</p><p class="no_wrap">09 — 10</p></span></th><th scope="col"><span><'
    'p class="no_wrap">Ora 11</p><p class="no_wrap">10 — 11</p></span></th><th scope="col"><spa'
    'n><p class="no_wrap">Ora 12</p><p class="no_wrap">11 — 12</p></span></th><th scope="col"><'
    'span><p class="no_wrap">Ora 13</p><p class="no_wrap">12 — 13</p></span></th><th scope="col'
    '"><span><p class="no_wrap">Ora 14</p><p class="no_wrap">13 — 14</p></span></th><th scope="'
    'col"><span><p class="no_wrap">Ora 15</p><p class="no_wrap">14 — 15</p></span></th><th scop'
    'e="col"><span><p class="no_wrap">Ora 16</p><p class="no_wrap">15 — 16</p></span></th><th s'
    'cope="col"><span><p class="no_wrap">Ora 17</p><p class="no_wrap">16 — 17</p></span></th><t'
    'h scope="col"><span><p class="no_wrap">Ora 18</p><p class="no_wrap">17 — 18</p></span></th'
    '><th scope="col"><span><p class="no_wrap">Ora 19</p><p class="no_wrap">18 — 19</p></span><'
    '/th><th scope="col"><span><p class="no_wrap">Ora 20</p><p class="no_wrap">19 — 20</p></spa'
    'n></th><th scope="col"><span><p class="no_wrap">Ora 21</p><p class="no_wrap">20 — 21</p></'
    'span></th><th scope="col"><span><p class="no_wrap">Ora 22</p><p class="no_wrap">21 — 22</p'
    '></span></th><th scope="col"><span><p class="no_wrap">Ora 23</p><p class="no_wrap">22 — 23'
    '</p></span></th><th scope="col"><span><p class="no_wrap">Ora 24</p><p class="no_wrap">23 —'
    ' 24</p></span></th><th scope="col"><span><p class="no_wrap">Ora 25</p><p class="no_wrap">0'
    '2 — 03</p></span></th></tr><tr align="right"><td align="center"><a class="no_wrap" href="j'
    "avascript:__doPostBack('ctl00$body$ctl00$ctl00$tcListUtenze$TCurveOrarie$cCurveOrarie$gvCu"
    'rveOrarieEE$ctl02$btnCurve\',\'\')" id="body_ctl00_ctl00_tcListUtenze_TCurveOrarie_cCurveOrar'
    'ie_gvCurveOrarieEE_btnCurve_0">01/10/2026 <i class="fa fa-info-circle"></i></a></td><td>0,'
    '200</td><td>0,200</td><td>0,200</td><td>0,200</td><td>0,200</td><td>0,200</td><td>0,200</t'
    'd><td>0,500</td><td>0,500</td><td>0,500</td><td>0,500</td><td>0,500</td><td>0,400</td><td>'
    '0,400</td><td>0,400</td><td>0,400</td><td>0,400</td><td>0,400</td><td>0,400</td><td>0,400<'
    '/td><td>0,400</td><td>0,400</td><td>0,400</td><td>0,400</td><td>0,000</td></tr><tr align="'
    'right"><td align="center"><a class="no_wrap" href="javascript:__doPostBack(\'ctl00$body$ctl'
    '00$ctl00$tcListUtenze$TCurveOrarie$cCurveOrarie$gvCurveOrarieEE$ctl03$btnCurve\',\'\')" id="b'
    'ody_ctl00_ctl00_tcListUtenze_TCurveOrarie_cCurveOrarie_gvCurveOrarieEE_btnCurve_1">02/10/2'
    '026 <i class="fa fa-info-circle"></i></a></td><td>0,300</td><td>0,300</td><td>0,300</td><t'
    'd>0,300</td><td>0,300</td><td>0,300</td><td>0,300</td><td>0,600</td><td>0,600</td><td>0,60'
    '0</td><td>0,600</td><td>0,600</td><td>0,500</td><td>0,500</td><td>0,500</td><td>0,500</td>'
    '<td>0,500</td><td>0,500</td><td>0,500</td><td>0,500</td><td>0,500</td><td>0,500</td><td>0,'
    '500</td><td>0,500</td><td>0,000</td></tr><tr align="right"><td align="center"><a class="no'
    '_wrap" href="javascript:__doPostBack(\'ctl00$body$ctl00$ctl00$tcListUtenze$TCurveOrarie$cCu'
    'rveOrarie$gvCurveOrarieEE$ctl04$btnCurve\',\'\')" id="body_ctl00_ctl00_tcListUtenze_TCurveOra'
    'rie_cCurveOrarie_gvCurveOrarieEE_btnCurve_2">24/10/2026 <i class="fa fa-info-circle"></i><'
    '/a></td><td>0,200</td><td>0,200</td><td>0,200</td><td>0,200</td><td>0,200</td><td>0,200</t'
    'd><td>0,200</td><td>0,500</td><td>0,500</td><td>0,500</td><td>0,500</td><td>0,500</td><td>'
    '0,400</td><td>0,400</td><td>0,400</td><td>0,400</td><td>0,400</td><td>0,400</td><td>0,400<'
    '/td><td>0,400</td><td>0,400</td><td>0,400</td><td>0,400</td><td>0,400</td><td>0,000</td></'
    'tr><tr align="right"><td align="center"><a class="no_wrap" href="javascript:__doPostBack(\''
    'ctl00$body$ctl00$ctl00$tcListUtenze$TCurveOrarie$cCurveOrarie$gvCurveOrarieEE$ctl05$btnCur'
    've\',\'\')" id="body_ctl00_ctl00_tcListUtenze_TCurveOrarie_cCurveOrarie_gvCurveOrarieEE_btnCu'
    'rve_3">25/10/2026 <i class="fa fa-info-circle"></i></a></td><td>0,200</td><td>0,200</td><t'
    'd>0,310</td><td>0,200</td><td>0,200</td><td>0,200</td><td>0,200</td><td>0,500</td><td>0,50'
    '0</td><td>0,500</td><td>0,500</td><td>0,500</td><td>0,400</td><td>0,400</td><td>0,400</td>'
    '<td>0,400</td><td>0,400</td><td>0,400</td><td>0,400</td><td>0,400</td><td>0,400</td><td>0,'
    '400</td><td>0,400</td><td>0,400</td><td>0,770</td></tr></table>'
)

# Un giorno da 23 ore (29/03/2026) come lo si presume: Ora 3 a zero. NON osservato.
GRIGLIA_ORARIA_PRIMAVERA = (
    '<table class="ps-gv" cellspacing="0" id="body_ctl00_ctl00_tcListUtenze_TCurveOrarie_cCurve'
    'Orarie_gvCurveOrarieEE"><tr class="UftrRptHead"><th scope="col"><span>Giorno</span></th><t'
    'h scope="col"><span><p class="no_wrap">Ora 1</p><p class="no_wrap">00 — 01</p></span></th>'
    '<th scope="col"><span><p class="no_wrap">Ora 2</p><p class="no_wrap">01 — 02</p></span></t'
    'h><th scope="col"><span><p class="no_wrap">Ora 3</p><p class="no_wrap">02 — 03</p></span><'
    '/th><th scope="col"><span><p class="no_wrap">Ora 4</p><p class="no_wrap">03 — 04</p></span'
    '></th><th scope="col"><span><p class="no_wrap">Ora 5</p><p class="no_wrap">04 — 05</p></sp'
    'an></th><th scope="col"><span><p class="no_wrap">Ora 6</p><p class="no_wrap">05 — 06</p></'
    'span></th><th scope="col"><span><p class="no_wrap">Ora 7</p><p class="no_wrap">06 — 07</p>'
    '</span></th><th scope="col"><span><p class="no_wrap">Ora 8</p><p class="no_wrap">07 — 08</'
    'p></span></th><th scope="col"><span><p class="no_wrap">Ora 9</p><p class="no_wrap">08 — 09'
    '</p></span></th><th scope="col"><span><p class="no_wrap">Ora 10</p><p class="no_wrap">09 —'
    ' 10</p></span></th><th scope="col"><span><p class="no_wrap">Ora 11</p><p class="no_wrap">1'
    '0 — 11</p></span></th><th scope="col"><span><p class="no_wrap">Ora 12</p><p class="no_wrap'
    '">11 — 12</p></span></th><th scope="col"><span><p class="no_wrap">Ora 13</p><p class="no_w'
    'rap">12 — 13</p></span></th><th scope="col"><span><p class="no_wrap">Ora 14</p><p class="n'
    'o_wrap">13 — 14</p></span></th><th scope="col"><span><p class="no_wrap">Ora 15</p><p class'
    '="no_wrap">14 — 15</p></span></th><th scope="col"><span><p class="no_wrap">Ora 16</p><p cl'
    'ass="no_wrap">15 — 16</p></span></th><th scope="col"><span><p class="no_wrap">Ora 17</p><p'
    ' class="no_wrap">16 — 17</p></span></th><th scope="col"><span><p class="no_wrap">Ora 18</p'
    '><p class="no_wrap">17 — 18</p></span></th><th scope="col"><span><p class="no_wrap">Ora 19'
    '</p><p class="no_wrap">18 — 19</p></span></th><th scope="col"><span><p class="no_wrap">Ora'
    ' 20</p><p class="no_wrap">19 — 20</p></span></th><th scope="col"><span><p class="no_wrap">'
    'Ora 21</p><p class="no_wrap">20 — 21</p></span></th><th scope="col"><span><p class="no_wra'
    'p">Ora 22</p><p class="no_wrap">21 — 22</p></span></th><th scope="col"><span><p class="no_'
    'wrap">Ora 23</p><p class="no_wrap">22 — 23</p></span></th><th scope="col"><span><p class="'
    'no_wrap">Ora 24</p><p class="no_wrap">23 — 24</p></span></th><th scope="col"><span><p clas'
    's="no_wrap">Ora 25</p><p class="no_wrap">02 — 03</p></span></th></tr><tr align="right"><td'
    ' align="center"><a class="no_wrap" href="javascript:__doPostBack(\'ctl00$body$ctl00$ctl00$t'
    'cListUtenze$TCurveOrarie$cCurveOrarie$gvCurveOrarieEE$ctl02$btnCurve\',\'\')" id="body_ctl00_'
    'ctl00_tcListUtenze_TCurveOrarie_cCurveOrarie_gvCurveOrarieEE_btnCurve_0">29/03/2026 <i cla'
    'ss="fa fa-info-circle"></i></a></td><td>0,200</td><td>0,200</td><td>0,000</td><td>0,200</t'
    'd><td>0,200</td><td>0,200</td><td>0,200</td><td>0,500</td><td>0,500</td><td>0,500</td><td>'
    '0,500</td><td>0,500</td><td>0,400</td><td>0,400</td><td>0,400</td><td>0,400</td><td>0,400<'
    '/td><td>0,400</td><td>0,400</td><td>0,400</td><td>0,400</td><td>0,400</td><td>0,400</td><t'
    'd>0,400</td><td>0,000</td></tr></table>'
)

# 03/10 con una cella vuota; 04/10 a zero in tutte le ore. Costruite a mano.
GRIGLIA_ORARIA_ANOMALA = (
    '<table class="ps-gv" cellspacing="0" id="body_ctl00_ctl00_tcListUtenze_TCurveOrarie_cCurve'
    'Orarie_gvCurveOrarieEE"><tr class="UftrRptHead"><th scope="col"><span>Giorno</span></th><t'
    'h scope="col"><span><p class="no_wrap">Ora 1</p><p class="no_wrap">00 — 01</p></span></th>'
    '<th scope="col"><span><p class="no_wrap">Ora 2</p><p class="no_wrap">01 — 02</p></span></t'
    'h><th scope="col"><span><p class="no_wrap">Ora 3</p><p class="no_wrap">02 — 03</p></span><'
    '/th><th scope="col"><span><p class="no_wrap">Ora 4</p><p class="no_wrap">03 — 04</p></span'
    '></th><th scope="col"><span><p class="no_wrap">Ora 5</p><p class="no_wrap">04 — 05</p></sp'
    'an></th><th scope="col"><span><p class="no_wrap">Ora 6</p><p class="no_wrap">05 — 06</p></'
    'span></th><th scope="col"><span><p class="no_wrap">Ora 7</p><p class="no_wrap">06 — 07</p>'
    '</span></th><th scope="col"><span><p class="no_wrap">Ora 8</p><p class="no_wrap">07 — 08</'
    'p></span></th><th scope="col"><span><p class="no_wrap">Ora 9</p><p class="no_wrap">08 — 09'
    '</p></span></th><th scope="col"><span><p class="no_wrap">Ora 10</p><p class="no_wrap">09 —'
    ' 10</p></span></th><th scope="col"><span><p class="no_wrap">Ora 11</p><p class="no_wrap">1'
    '0 — 11</p></span></th><th scope="col"><span><p class="no_wrap">Ora 12</p><p class="no_wrap'
    '">11 — 12</p></span></th><th scope="col"><span><p class="no_wrap">Ora 13</p><p class="no_w'
    'rap">12 — 13</p></span></th><th scope="col"><span><p class="no_wrap">Ora 14</p><p class="n'
    'o_wrap">13 — 14</p></span></th><th scope="col"><span><p class="no_wrap">Ora 15</p><p class'
    '="no_wrap">14 — 15</p></span></th><th scope="col"><span><p class="no_wrap">Ora 16</p><p cl'
    'ass="no_wrap">15 — 16</p></span></th><th scope="col"><span><p class="no_wrap">Ora 17</p><p'
    ' class="no_wrap">16 — 17</p></span></th><th scope="col"><span><p class="no_wrap">Ora 18</p'
    '><p class="no_wrap">17 — 18</p></span></th><th scope="col"><span><p class="no_wrap">Ora 19'
    '</p><p class="no_wrap">18 — 19</p></span></th><th scope="col"><span><p class="no_wrap">Ora'
    ' 20</p><p class="no_wrap">19 — 20</p></span></th><th scope="col"><span><p class="no_wrap">'
    'Ora 21</p><p class="no_wrap">20 — 21</p></span></th><th scope="col"><span><p class="no_wra'
    'p">Ora 22</p><p class="no_wrap">21 — 22</p></span></th><th scope="col"><span><p class="no_'
    'wrap">Ora 23</p><p class="no_wrap">22 — 23</p></span></th><th scope="col"><span><p class="'
    'no_wrap">Ora 24</p><p class="no_wrap">23 — 24</p></span></th><th scope="col"><span><p clas'
    's="no_wrap">Ora 25</p><p class="no_wrap">02 — 03</p></span></th></tr><tr align="right"><td'
    ' align="center"><a class="no_wrap" href="javascript:__doPostBack(\'ctl00$body$ctl00$ctl00$t'
    'cListUtenze$TCurveOrarie$cCurveOrarie$gvCurveOrarieEE$ctl02$btnCurve\',\'\')" id="body_ctl00_'
    'ctl00_tcListUtenze_TCurveOrarie_cCurveOrarie_gvCurveOrarieEE_btnCurve_0">03/10/2026 <i cla'
    'ss="fa fa-info-circle"></i></a></td><td>0,200</td><td>0,200</td><td>0,200</td><td>0,200</t'
    'd><td>0,200</td><td></td><td>0,200</td><td>0,500</td><td>0,500</td><td>0,500</td><td>0,500'
    '</td><td>0,500</td><td>0,400</td><td>0,400</td><td>0,400</td><td>0,400</td><td>0,400</td><'
    'td>0,400</td><td>0,400</td><td>0,400</td><td>0,400</td><td>0,400</td><td>0,400</td><td>0,4'
    '00</td><td>0,000</td></tr><tr align="right"><td align="center"><a class="no_wrap" href="ja'
    "vascript:__doPostBack('ctl00$body$ctl00$ctl00$tcListUtenze$TCurveOrarie$cCurveOrarie$gvCur"
    'veOrarieEE$ctl03$btnCurve\',\'\')" id="body_ctl00_ctl00_tcListUtenze_TCurveOrarie_cCurveOrari'
    'e_gvCurveOrarieEE_btnCurve_1">04/10/2026 <i class="fa fa-info-circle"></i></a></td><td>0,0'
    '00</td><td>0,000</td><td>0,000</td><td>0,000</td><td>0,000</td><td>0,000</td><td>0,000</td'
    '><td>0,000</td><td>0,000</td><td>0,000</td><td>0,000</td><td>0,000</td><td>0,000</td><td>0'
    ',000</td><td>0,000</td><td>0,000</td><td>0,000</td><td>0,000</td><td>0,000</td><td>0,000</'
    'td><td>0,000</td><td>0,000</td><td>0,000</td><td>0,000</td><td>0,000</td></tr></table>'
)



_CAMPI_NASCOSTI = (
    '<input type="hidden" name="__EVENTTARGET" id="__EVENTTARGET" value="" />'
    '<input type="hidden" name="__EVENTARGUMENT" id="__EVENTARGUMENT" value="" />'
    '<input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="VS-{n}" />'
    '<input type="hidden" name="__VIEWSTATEGENERATOR" id="__VIEWSTATEGENERATOR" value="BDD8F411" />'
    '<input type="hidden" name="__EVENTVALIDATION" id="__EVENTVALIDATION" value="EV-{n}" />'
)


def pagina(corpo: str, n: int = 1, *, utenti_selezionati: bool = True) -> str:
    """Una pagina del portale: form fMain con i campi nascosti ASP.NET, un
    pulsante submit (che il browser non invia) e il 'corpo'."""
    return (
        '<html><head><title>Enterprise Integration Platform</title></head><body>'
        '<form method="post" action="./Single.tws" id="fMain">'
        + _CAMPI_NASCOSTI.format(n=n)
        + '<input type="submit" name="ctl00$btnDefault" value="" id="btnDefault" />'
        + corpo
        + "</form></body></html>"
    )


def pagina_home(n: int = 1) -> str:
    return pagina("<div id='menu'>" + MENU_UTENZE_E_COMUNICAZIONI + "</div>", n)


def pagina_utenze(griglia: str = GRIGLIA_UTENZE, n: int = 2) -> str:
    return pagina(
        "<div id='menu'>" + MENU_UTENZE_E_COMUNICAZIONI + "</div>"
        + INPUT_RICERCA_UTENZE
        + griglia,
        n,
    )


def pagina_utenze_vuota(n: int = 2) -> str:
    """Account senza POD: la ricerca c'è, nessuna griglia."""
    return pagina("<div id='menu'>" + MENU_UTENZE_E_COMUNICAZIONI + "</div>" + INPUT_RICERCA_UTENZE, n)


def pagina_curve(
    griglia: str = GRIGLIA_UTENZE,
    selettore: str = SELETTORE_ANNO,
    n: int = 3,
    tabella: str = TABELLA_MESI,
) -> str:
    return pagina(
        "<div id='menu'>" + MENU_UTENZE_E_COMUNICAZIONI + "</div>"
        + INPUT_RICERCA_UTENZE + griglia
        + INTESTAZIONE_TAB_CURVE + selettore + tabella,
        n,
    )


def pagina_curve_orarie(
    griglia_oraria: str = GRIGLIA_ORARIA_OTTOBRE,
    *,
    mese: str = "10/2026",
    selettore: str = SELETTORE_ANNO,
    intestazione_orarie: str = INTESTAZIONE_TAB_CURVE_ORARIE,
    n: int = 4,
) -> str:
    """Come la risposta catturata: tutti i tab aperti (Utenze, Curve, Curve orarie)."""
    return pagina(
        "<div id='menu'>" + MENU_UTENZE_E_COMUNICAZIONI + "</div>"
        + INPUT_RICERCA_UTENZE + GRIGLIA_UTENZE
        + INTESTAZIONE_TAB_CURVE + selettore + TABELLA_MESI
        + intestazione_orarie
        + f"<fieldset><legend> Consumi del mese: {mese} </legend></fieldset>"
        + griglia_oraria,
        n,
    )


def selettore_anno(anno: int) -> str:
    """SELETTORE_ANNO con un altro anno selezionato."""
    return SELETTORE_ANNO.replace(' selected="selected"', "").replace(
        f'<option value="{anno}">', f'<option selected="selected" value="{anno}">'
    )


# --- Login ------------------------------------------------------------------------------

MODULO_LOGIN = (
    '<html><body><form method="post" action="./Login.tws?ReturnUrl=x" id="fMain">'
    '<input type="hidden" name="__LASTFOCUS" value="" />'
    '<input type="hidden" name="__EVENTTARGET" value="" />'
    '<input type="hidden" name="__EVENTARGUMENT" value="" />'
    '<input type="hidden" name="__VIEWSTATE" value="VS-LOGIN" />'
    '<input type="hidden" name="__VIEWSTATEGENERATOR" value="E2E44E03" />'
    '<input type="hidden" name="__VIEWSTATEENCRYPTED" value="" />'
    '<input type="hidden" name="__PREVIOUSPAGE" value="PP-LOGIN" />'
    '<input type="hidden" name="__EVENTVALIDATION" value="EV-LOGIN" />'
    '<input name="ctl00$ctl00$body$body$cLogin$txtUser" type="text" />'
    '<input name="ctl00$ctl00$body$body$cLogin$txtPassword" type="password" />'
    '<input type="submit" name="ctl00$ctl00$body$body$cLogin$btnLogin" value="Accedi" />'
    "</form></body></html>"
)

# Risposte UpdatePanel ('len|tipo|id|contenuto|'), nel formato catturato.
RISPOSTA_LOGIN_OK = "1|#||4|52|pageRedirect||%2fEIPPUF%2fEIPPUF%2fit-IT%2fdD4%2fPage%2fSingle.tws|"
_PANNELLO_ERRORE = (
    "<span>Errore Autenticazione: Credenziali di accesso non valide</span>"
)
RISPOSTA_LOGIN_RIFIUTATA = (
    f"1|#||4|{len(_PANNELLO_ERRORE)}|updatePanel|cMessageBox_upMBox|{_PANNELLO_ERRORE}|"
)
