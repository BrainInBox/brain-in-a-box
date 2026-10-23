# Pouls du job (jetable) : le premier passage a perdu ses deux runners Windows sans un seul journal.
# Toutes les 60 s : la phase en cours, la mémoire, le disque et les plus gros consommateurs de CPU,
# dans un fichier (téléversé avec les artefacts) et dans un statut de commit, lisible pendant le job.
$ErrorActionPreference = "Continue"
$Out = Join-Path $env:RUNNER_TEMP "h1out"
New-Item -ItemType Directory -Force $Out | Out-Null
$Log = Join-Path $Out "heartbeat.log"
$Uri = "https://api.github.com/repos/$env:GITHUB_REPOSITORY/statuses/$env:GITHUB_SHA"
$Ctx = "h1-pouls-$env:H1_OS"
$Token = $env:HB_TOKEN
Remove-Item Env:HB_TOKEN -ErrorAction SilentlyContinue
$prev = @{}
while ($true) {
    try {
        $phase = (Get-Content (Join-Path $Out "phase.txt") -ErrorAction SilentlyContinue | Select-Object -First 1)
        $os = Get-CimInstance Win32_OperatingSystem
        $mem = [math]::Round($os.FreePhysicalMemory / 1MB, 1)
        $c = [math]::Round((Get-PSDrive C).Free / 1GB, 1)
        $d = try { [math]::Round((Get-PSDrive D -ErrorAction Stop).Free / 1GB, 1) } catch { "-" }
        $cpu = [math]::Round((Get-CimInstance Win32_Processor | Measure-Object LoadPercentage -Average).Average)
        # CPU pris depuis le dernier relevé, par processus
        $now = @{}
        foreach ($p in Get-Process) { try { $now["$($p.Id)"] = @($p.ProcessName, $p.TotalProcessorTime.TotalSeconds) } catch {} }
        $delta = foreach ($k in $now.Keys) { $b = if ($prev[$k]) { $prev[$k][1] } else { 0 }; [pscustomobject]@{ n = $now[$k][0]; s = $now[$k][1] - $b } }
        $prev = $now
        $top = ($delta | Sort-Object s -Descending | Select-Object -First 3 | ForEach-Object { "$($_.n):$([math]::Round($_.s))" }) -join ","
        $line = "$((Get-Date).ToUniversalTime().ToString('HH:mm:ss')) $phase | mem ${mem}G C ${c}G D ${d}G cpu $cpu% | $top"
        Add-Content -Path $Log -Value $line
        if ($Token) {
            $desc = if ($line.Length -gt 140) { $line.Substring(0, 140) } else { $line }
            $body = @{ state = "pending"; context = $Ctx; description = $desc } | ConvertTo-Json
            Invoke-RestMethod -Method Post -Uri $Uri -Body $body -ContentType "application/json" -TimeoutSec 20 `
                -Headers @{ Authorization = "Bearer $Token"; Accept = "application/vnd.github+json" } | Out-Null
        }
    } catch {
        Add-Content -Path $Log -Value "heartbeat error: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds 60
}
