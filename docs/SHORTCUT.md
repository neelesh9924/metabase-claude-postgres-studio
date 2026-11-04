# Desktop shortcut

The app does not create shortcuts itself. Run this once in PowerShell to put
**Metabase Claude Studio** on your desktop. It points at the `.vbs` launcher (no console
window) and uses `assets\studio.ico`.

```powershell
$root  = "C:\path\to\metabase-claude-postgres-studio"   # the folder that holds "Launch Metabase Claude Studio.vbs"
$shell = New-Object -ComObject WScript.Shell
$link  = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "Metabase Claude Studio.lnk"))
$link.TargetPath       = Join-Path $env:SystemRoot "System32\wscript.exe"
$link.Arguments        = "`"$root\Launch Metabase Claude Studio.vbs`""
$link.WorkingDirectory = $root
$link.IconLocation     = "$root\assets\studio.ico,0"
$link.Description      = "Metabase Claude Studio"
$link.Save()
```

`GetFolderPath("Desktop")` follows OneDrive desktop redirection, so the shortcut lands on
the desktop you see. For the Start menu too, run it again with `"Desktop"` changed to
`"Programs"`.

To pin it to the taskbar, right-click the shortcut, "Show more options", "Pin to taskbar".

To remove it, delete `Metabase Claude Studio.lnk` from the desktop.
