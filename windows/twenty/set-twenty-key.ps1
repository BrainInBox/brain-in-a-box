# set-twenty-key.ps1 - Enregistre l'acces API Twenty (CRM) pour le connecteur brain.
#
# Lance-le dans TON terminal (la cle est saisie ici, masquee, jamais affichee
# ni envoyee a Claude) :
#   powershell -ExecutionPolicy Bypass -File set-twenty-key.ps1
#
# Ecrit dans ~/.gbrain/twenty/config.json : { url, api_key }

$ErrorActionPreference = "Stop"

$dir = Join-Path $env:USERPROFILE ".gbrain\twenty"
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
$cfgPath = Join-Path $dir "config.json"

$url = Read-Host "URL de ton instance Twenty (defaut: https://crm.citymood.io)"
if ([string]::IsNullOrWhiteSpace($url)) { $url = "https://crm.citymood.io" }
$url = $url.TrimEnd('/') -replace '/rest$',''   # on garde l'URL de BASE (le /rest est ajoute par le code)

$sec = Read-Host "Colle ta cle API Twenty (le token COMPLET, type eyJ...)" -AsSecureString
$key = ([System.Net.NetworkCredential]::new("", $sec).Password).Trim()
if ([string]::IsNullOrWhiteSpace($key)) { Write-Host "Cle vide -> abandon." -ForegroundColor Red; exit 1 }
if ($key -match '[^\x20-\x7E]') {
  Write-Host "Cle invalide (caracteres non-ASCII / bullets •). Tu as sans doute copie une version MASQUEE." -ForegroundColor Red
  Write-Host "Recopie la cle COMPLETE telle qu'affichee a la creation (longue, commence souvent par 'eyJ')." -ForegroundColor Yellow
  exit 1
}

$cfg = [ordered]@{ url = $url; api_key = $key }
$json = ($cfg | ConvertTo-Json -Depth 5)
[System.IO.File]::WriteAllText($cfgPath, $json, [System.Text.UTF8Encoding]::new($false))

# ACL : lisible par toi seul.
icacls $cfgPath /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null

Write-Host "OK: acces Twenty enregistre dans $cfgPath (ACL durcie)." -ForegroundColor Green
Write-Host "La cle n'a pas ete affichee. Reviens dans Claude et dis 'cle twenty ok'."
