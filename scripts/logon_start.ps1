# Optional: start Relay Employee Hub in background mode when the operator signs in to Windows.
#   powershell -ExecutionPolicy Bypass -File scripts\logon_start.ps1 -Enable
#   powershell -ExecutionPolicy Bypass -File scripts\logon_start.ps1 -Disable
#
# This uses the operator's Startup folder, so the app (and Outlook automation) runs inside the
# operator's own Windows session. It is deliberately NOT a Windows service: Outlook automation
# must not run unattended as a service. Scheduled tasks only run while this computer is on,
# the operator is signed in, and the app is running.
param([switch]$Enable, [switch]$Disable)
$startup = [Environment]::GetFolderPath("Startup")
$lnk = Join-Path $startup "Relay Employee Hub (background).lnk"
if ($Disable) {
  if (Test-Path $lnk) { Remove-Item $lnk; Write-Host "Logon start removed." } else { Write-Host "Logon start was not enabled." }
  exit 0
}
if ($Enable) {
  & (Join-Path $PSScriptRoot "create_shortcut.ps1") -Startup
  exit $LASTEXITCODE
}
Write-Host "Use -Enable or -Disable."
