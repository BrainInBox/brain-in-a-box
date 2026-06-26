# set-anthropic-key.ps1 - Configure la cle Anthropic pour la synthese gbrain (Windows).
#
# Sert UNIQUEMENT a `gbq query` (reponses redigees a partir de ta memoire).
# Le journal et la recherche n'en ont PAS besoin.
#
# Lance-le dans TON terminal (la cle est saisie ici, masquee, jamais affichee
# ni envoyee a Claude) :
#   powershell -ExecutionPolicy Bypass -File set-anthropic-key.ps1
#
# Il ecrit la cle dans ~/.gbrain/config.json (champ anthropic_api_key).

$ErrorActionPreference = "Stop"
$cfgPath = Join-Path $env:USERPROFILE ".gbrain\config.json"
if (-not (Test-Path $cfgPath)) { Write-Host "config.json introuvable ($cfgPath). gbrain init d'abord." -ForegroundColor Red; exit 1 }

$sec = Read-Host "Colle ta cle Anthropic (sk-ant-...)" -AsSecureString
$key = [System.Net.NetworkCredential]::new("", $sec).Password
if ([string]::IsNullOrWhiteSpace($key)) { Write-Host "Cle vide -> abandon." -ForegroundColor Red; exit 1 }

$cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
$cfg | Add-Member -NotePropertyName anthropic_api_key -NotePropertyValue $key -Force

$json = $cfg | ConvertTo-Json -Depth 20
[System.IO.File]::WriteAllText($cfgPath, $json, [System.Text.UTF8Encoding]::new($false))

# Durcissement ACL : lisible par toi seul.
icacls $cfgPath /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null

Write-Host "OK: cle Anthropic enregistree (synthese gbq query activee)." -ForegroundColor Green
Write-Host "La cle n'a pas ete affichee. Reviens dans Claude et dis 'cle ok'."
