# set-ze-key.ps1 - Configure la cle ZeroEntropy pour gbrain (Windows).
#
# Lance-le dans TON terminal (la cle est saisie ici, masquee, jamais affichee
# ni envoyee a Claude) :
#   powershell -ExecutionPolicy Bypass -File set-ze-key.ps1
#
# Il ecrit la cle dans ~/.gbrain/config.json (champ zeroentropy_api_key),
# active l'embedding et fixe le modele zeroentropyai:zembed-1.

$ErrorActionPreference = "Stop"
$cfgPath = Join-Path $env:USERPROFILE ".gbrain\config.json"
if (-not (Test-Path $cfgPath)) { Write-Host "config.json introuvable ($cfgPath). gbrain init d'abord." -ForegroundColor Red; exit 1 }

$sec = Read-Host "Colle ta cle ZeroEntropy (ze_...)" -AsSecureString
$key = [System.Net.NetworkCredential]::new("", $sec).Password
if ([string]::IsNullOrWhiteSpace($key)) { Write-Host "Cle vide -> abandon." -ForegroundColor Red; exit 1 }

$cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
$cfg | Add-Member -NotePropertyName zeroentropy_api_key -NotePropertyValue $key -Force
$cfg | Add-Member -NotePropertyName embedding_model -NotePropertyValue "zeroentropyai:zembed-1" -Force
if ($cfg.PSObject.Properties.Name -contains 'embedding_disabled') { $cfg.PSObject.Properties.Remove('embedding_disabled') }

$json = $cfg | ConvertTo-Json -Depth 20
[System.IO.File]::WriteAllText($cfgPath, $json, [System.Text.UTF8Encoding]::new($false))

Write-Host "OK: cle enregistree, embedding active (zeroentropyai:zembed-1)." -ForegroundColor Green
Write-Host "La cle n'a pas ete affichee. Reviens dans Claude et dis 'cle ok'."
