# set-google-oauth.ps1 - Enregistre les creds OAuth Google (Gmail readonly) du collecteur.
#
# Lance-le dans TON terminal (creds saisis ici, masques, jamais affiches ni
# envoyes a Claude) :
#   powershell -ExecutionPolicy Bypass -File set-google-oauth.ps1
#
# Ecrit dans ~/.gbrain/email-collector/config.json :
#   { "client_id": "...", "client_secret": "...", "account": "mohamedfakhoury@citymood.io" }
# Le refresh token sera ajoute par le collecteur au 1er run (flux OAuth navigateur).

$ErrorActionPreference = "Stop"

$dir = Join-Path $env:USERPROFILE ".gbrain\email-collector"
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
$cfgPath = Join-Path $dir "config.json"

$account = Read-Host "Ton adresse mail a connecter (defaut: mohamedfakhoury@citymood.io)"
if ([string]::IsNullOrWhiteSpace($account)) { $account = "mohamedfakhoury@citymood.io" }

$idSec = Read-Host "Colle le Client ID (xxxx.apps.googleusercontent.com)" -AsSecureString
$clientId = [System.Net.NetworkCredential]::new("", $idSec).Password
if ([string]::IsNullOrWhiteSpace($clientId)) { Write-Host "Client ID vide -> abandon." -ForegroundColor Red; exit 1 }

$secSec = Read-Host "Colle le Client Secret" -AsSecureString
$clientSecret = [System.Net.NetworkCredential]::new("", $secSec).Password
if ([string]::IsNullOrWhiteSpace($clientSecret)) { Write-Host "Client Secret vide -> abandon." -ForegroundColor Red; exit 1 }

# Preserve un eventuel refresh_token deja present.
$existing = @{}
if (Test-Path $cfgPath) {
  try { $existing = Get-Content $cfgPath -Raw | ConvertFrom-Json } catch { $existing = $null }
  if ($null -eq $existing) { $existing = @{} }
}

$cfg = [ordered]@{
  account       = $account
  client_id     = $clientId
  client_secret = $clientSecret
}
if ($existing.PSObject -and ($existing.PSObject.Properties.Name -contains 'refresh_token')) {
  $cfg.refresh_token = $existing.refresh_token
}

$json = ($cfg | ConvertTo-Json -Depth 10)
[System.IO.File]::WriteAllText($cfgPath, $json, [System.Text.UTF8Encoding]::new($false))

# ACL : lisible par toi seul.
icacls $cfgPath /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null

Write-Host "OK: creds enregistres dans $cfgPath (ACL durcie)." -ForegroundColor Green
Write-Host "Les creds n'ont pas ete affiches. Reviens dans Claude et dis 'creds ok'."
