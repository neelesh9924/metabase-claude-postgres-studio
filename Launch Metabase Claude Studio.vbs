' Starts Dashboard Studio with no console window.
' First run (no .venv yet): opens a visible console that builds the venv, then starts the app.
Option Explicit
Dim fso, sh, here, pyw
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here
pyw = here & "\.venv\Scripts\pythonw.exe"
If fso.FileExists(pyw) Then
  sh.Run """" & pyw & """ """ & here & "\launch.py""", 0, False
Else
  sh.Run "py.exe -3 """ & here & "\launch.py"" --setup", 1, False
End If
