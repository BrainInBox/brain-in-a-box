# Throwaway (spike/guide-v2-uia): a RemoteApp (then, failing that, a whole remote desktop) opened by
# mstsc on the VM itself, to a user of the job's, for Bibo's UIA list to meet. The password is drawn
# here, kept in Windows' credential store for mstsc, never written to the log; the user dies with the VM.
param([ValidateSet('remoteapp', 'bureau')][string]$Mode = 'remoteapp')
$ErrorActionPreference = 'Stop'
$ts = 'HKLM:\System\CurrentControlSet\Control\Terminal Server'
Set-ItemProperty $ts fDenyTSConnections 0
Set-ItemProperty "$ts\WinStations\RDP-Tcp" UserAuthentication 0
$allow = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Terminal Server\TSAppAllowList'
New-Item $allow -Force | Out-Null
Set-ItemProperty $allow fDisabledAllowList 1
$pol = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services'
New-Item $pol -Force | Out-Null
Set-ItemProperty $pol fAllowUnlistedRemotePrograms 1
New-Item 'HKCU:\Software\Microsoft\Terminal Server Client' -Force | Out-Null
Set-ItemProperty 'HKCU:\Software\Microsoft\Terminal Server Client' AuthenticationLevelOverride 0
Set-Service TermService -StartupType Manual
Start-Service TermService
"TermService: $((Get-Service TermService).Status)"

$bytes = New-Object byte[] 24
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$pw = 'Aa1!' + [Convert]::ToBase64String($bytes)
if (-not (Get-LocalUser -Name biboloin -ErrorAction SilentlyContinue)) {
  New-LocalUser -Name biboloin -Password (ConvertTo-SecureString $pw -AsPlainText -Force) -PasswordNeverExpires | Out-Null
  Add-LocalGroupMember -Group 'Remote Desktop Users' -Member biboloin
  cmdkey /generic:TERMSRV/127.0.0.2 /user:"$env:COMPUTERNAME\biboloin" /pass:$pw | Out-Null
}
Remove-Variable pw

$rdp = Join-Path $env:RUNNER_TEMP "loin-$Mode.rdp"
$lines = @(
  'full address:s:127.0.0.2', "username:s:$env:COMPUTERNAME\biboloin", 'authentication level:i:0', 'prompt for credentials:i:0',
  'promptcredentialonce:i:0', 'enablecredsspsupport:i:1', 'redirectclipboard:i:0', 'redirectprinters:i:0',
  'redirectdrives:i:0', 'audiomode:i:2', 'screen mode id:i:1', 'desktopwidth:i:900', 'desktopheight:i:650'
)
if ($Mode -eq 'remoteapp') {
  $lines += 'remoteapplicationmode:i:1', 'remoteapplicationprogram:s:C:\Windows\System32\notepad.exe', 'remoteapplicationname:s:Bloc-notes distant', 'disableremoteappcapscheck:i:1'
}
$lines | Out-File -FilePath $rdp -Encoding ascii
Start-Process mstsc.exe -ArgumentList "`"$rdp`""
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$A = [System.Windows.Automation.AutomationElement]
Add-Type -Namespace Loin -Name Main -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
[DllImport("user32.dll")] public static extern void mouse_event(uint f, uint x, uint y, uint d, IntPtr e);
[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
'@
[Loin.Main]::SetProcessDPIAware() | Out-Null
function Click($el) {
  $r = $el.Current.BoundingRectangle
  [Loin.Main]::SetCursorPos([int]($r.X + $r.Width / 2), [int]($r.Y + $r.Height / 2)) | Out-Null
  [Loin.Main]::mouse_event(2, 0, 0, 0, [IntPtr]::Zero); [Loin.Main]::mouse_event(4, 0, 0, 0, [IntPtr]::Zero)
}
# The warnings mstsc shows for a file of no publisher (since 2026: a box to tick, then OK), or for a
# certificate of its own: "Connect" / "Yes".
for ($i = 0; $i -lt 90; $i++) {
  Start-Sleep -Milliseconds 1000
  foreach ($w in $A::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)) {
    $c = $w.Current
    $proc = (Get-Process -Id $c.ProcessId -ErrorAction SilentlyContinue).ProcessName
    if ($proc -ne 'mstsc' -or $c.ClassName -ne '#32770') { continue }
    $all = $w.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($b in $all) {
      $bc = $b.Current
      if ($bc.ControlType -eq [System.Windows.Automation.ControlType]::CheckBox -and $bc.Name -like 'I understand*') {
        $t = $b.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
        if ($t.Current.ToggleState -ne 'On') { "tick '$($bc.Name.Substring(0, 30))...'"; $t.Toggle() }
      }
    }
    foreach ($b in $all) {
      if ($b.Current.Name -in 'OK', 'Connect', '&Connect', 'Yes', '&Yes') {
        "click '$($b.Current.Name)' in '$($c.Name)'"
        Click $b
        Start-Sleep -Milliseconds 500
      }
    }
  }
  $wins = $A::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition) | Where-Object { (Get-Process -Id $_.Current.ProcessId -ErrorAction SilentlyContinue).ProcessName -eq 'mstsc' }
  $names = $wins | ForEach-Object { "$($_.Current.ClassName)|$($_.Current.Name)" }
  if ($i % 10 -eq 0) { "t=$i s: mstsc windows: $($names -join ' ; ')" }
  if ($Mode -eq 'remoteapp' -and ($wins | Where-Object { $_.Current.ClassName -eq 'RAIL_WINDOW' -and $_.Current.Name -match 'Notepad|Bloc' })) { "RAIL window up at $i s"; break }
  if ($Mode -eq 'bureau' -and $i -ge 40) { break }
}
quser 2>&1
