import win32com.client
import subprocess
import time
import os
import dotenv

dotenv.load_dotenv()

subprocess.Popen(os.getenv("SAP_GUI_PATH"))
time.sleep(15)
sapgui_auto = win32com.client.GetObject("SAPGUI")
application = sapgui_auto.GetScriptingEngine
connection = application.OpenConnection(os.getenv("connection"), True)

# 連接到SAP GUI(登入)
session = connection.Children(0)
session.findByID("wnd[0]/usr/txtRSYST-MANDT").text = os.getenv("MANDT")
session.findByID("wnd[0]/usr/txtRSYST-BNAME").text = os.getenv("BNAME")
session.findByID("wnd[0]/usr/pwdRSYST-BCODE").text = os.getenv("BCODE")
session.findByID("wnd[0]").sendVKey(0)

# SAP 操作指令
# session.findByID("wnd[0]").maximize()
# session.findByID("wnd[0]/tbar[0]/okcd").text = "se11"
# session.findByID("wnd[0]").sendVKey(0)
# session.findByID("wnd[0]/usr/radRSRD1-DOMA").setFocus()
# session.findByID('/app/con[0]/ses[0]/wnd[0]/usr/radRSRD1-DOMA').select()
# session.findByID('/app/con[0]/ses[0]/wnd[0]/usr/ctxtRSRD1-DOMA_VAL').text = "Z_A"
# session.findByID("wnd[0]/usr/ctxtRSRD1-DOMA_VAL").caretPosition = 11
# session.findByID("wnd[0]/usr/btnPUSHADD").press()
# session.findByID("wnd[0]/usr/txtDD01D-DDTEXT").text = "auto script test"
# session.findByID("wnd[0]/usr/tabsTAB_STRIP/tabpTAB1/ssubTS_SCREEN:SAPLSD11:1201/ctxtDD01D-DATATYPE").text = "C*"
# session.findByID("wnd[0]/usr/tabsTAB_STRIP/tabpTAB1/ssubTS_SCREEN:SAPLSD11:1201/ctxtDD01D-DATATYPE").setFocus()
# session.findByID("wnd[0]/usr/tabsTAB_STRIP/tabpTAB1/ssubTS_SCREEN:SAPLSD11:1201/ctxtDD01D-DATATYPE").caretPosition = 2
# session.findByID("wnd[0]").sendVKey(4)
# session.findByID("wnd[1]/usr/lbl[1,3]").caretPosition = 3
# session.findByID("wnd[1]").sendVKey(2)
# session.findByID("wnd[0]/usr/tabsTAB_STRIP/tabpTAB1/ssubTS_SCREEN:SAPLSD11:1201/txtDD01D-LENG").text = "30"
# session.findByID("wnd[0]/usr/tabsTAB_STRIP/tabpTAB1/ssubTS_SCREEN:SAPLSD11:1201/txtDD01D-LENG").setFocus()
# session.findByID("wnd[0]/usr/tabsTAB_STRIP/tabpTAB1/ssubTS_SCREEN:SAPLSD11:1201/txtDD01D-LENG").caretPosition = 6
# session.findByID("wnd[0]").sendVKey(0)
# session.findByID("wnd[0]/tbar[1]/btn[26]").press()
# session.findByID("wnd[1]/usr/btnSPOP-OPTION1").press()
# session.findByID("wnd[1]/tbar[0]/btn[7]").press()
# session.findByID("wnd[0]/tbar[1]/btn[27]").press()
# session.findByID("wnd[1]/tbar[0]/btn[0]").press()
# session.findByID("wnd[0]").sendVKey(12)