# Creates a desktop shortcut for Relay Employee Hub.
#   -Mode source    : shortcut to the source installation (.venv\Scripts\pythonw.exe launcher\run_hub.py)
#   -Mode exe       : shortcut to the packaged RelayEmployeeHub.exe (default when the exe exists)
#   -Startup        : create the shortcut in the Windows Startup folder instead, starting in background mode
param(
  [ValidateSet("auto", "source", "exe")] [string]$Mode = "auto",
  [switch]$Startup
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (Test-Path (Join-Path $PSScriptRoot "RelayEmployeeHub.exe")) { $root = $PSScriptRoot }
$exe = Join-Path $root "RelayEmployeeHub.exe"
if ($Mode -eq "auto") { if (Test-Path $exe) { $Mode = "exe" } else { $Mode = "source" } }

if ($Mode -eq "exe") {
  $target = $exe; $arguments = ""; $icon = $exe
} else {
  $target = Join-Path $root ".venv\Scripts\pythonw.exe"
  $arguments = '"' + (Join-Path $root "launcher\run_hub.py") + '"'
  $icon = Join-Path $root "packaging\relay_hub.ico"
}
if (-not (Test-Path $target)) { Write-Error "Not found: $target. Run the setup or build first." }

if ($Startup) {
  $folder = [Environment]::GetFolderPath("Startup")
  $name = "Relay Employee Hub (background).lnk"
  $arguments = ($arguments + " --background").Trim()
} else {
  $folder = [Environment]::GetFolderPath("Desktop")
  $name = "Relay Employee Hub.lnk"
}
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut((Join-Path $folder $name))
$lnk.TargetPath = $target
$lnk.Arguments = $arguments
$lnk.WorkingDirectory = $root
$lnk.IconLocation = $icon
$lnk.Description = "Relay Employee Hub"
$lnk.Save()
Write-Host "Shortcut created: $(Join-Path $folder $name)"
