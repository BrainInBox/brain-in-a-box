# A RemoteApp on the CI VM itself, for the guide v2's elements to meet a remote desktop (test.yml,
# app-uia: uia_remoteapp). mstsc opens Notepad of a local user of the job's, as a RemoteApp, from
# 127.0.0.2. The user's password is drawn here, given to Windows' credential store for mstsc and never
# written to the log; the user goes with the VM. Then the warnings mstsc shows for a file of no
# publisher (since 2026: a box to tick, then OK) are clicked, until Notepad's RemoteApp window is up.
#
#   ./.github/ci/remoteapp.ps1          from the job, on a throwaway VM only
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

$rdp = Join-Path $env:RUNNER_TEMP 'loin.rdp'
@(
  'full address:s:127.0.0.2', "username:s:$env:COMPUTERNAME\biboloin", 'authentication level:i:0', 'prompt for credentials:i:0',
  'promptcredentialonce:i:0', 'enablecredsspsupport:i:1', 'redirectclipboard:i:0', 'redirectprinters:i:0',
  'redirectdrives:i:0', 'audiomode:i:2', 'screen mode id:i:1', 'desktopwidth:i:900', 'desktopheight:i:650',
  'remoteapplicationmode:i:1', 'remoteapplicationprogram:s:C:\Windows\System32\notepad.exe',
  'remoteapplicationname:s:Bloc-notes distant', 'disableremoteappcapscheck:i:1'
) | Out-File -FilePath $rdp -Encoding ascii
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
$all = [System.Windows.Automation.TreeScope]::Children
$any = [System.Windows.Automation.Condition]::TrueCondition
for ($i = 0; $i -lt 90; $i++) {
  Start-Sleep -Milliseconds 1000
  foreach ($w in $A::RootElement.FindAll($all, $any)) {
    $c = $w.Current
    if ((Get-Process -Id $c.ProcessId -ErrorAction SilentlyContinue).ProcessName -ne 'mstsc' -or $c.ClassName -ne '#32770') { continue }
    $inside = $w.FindAll([System.Windows.Automation.TreeScope]::Descendants, $any)
    foreach ($b in $inside) {
      $bc = $b.Current
      if ($bc.ControlType -eq [System.Windows.Automation.ControlType]::CheckBox -and $bc.Name -like 'I understand*') {
        $t = $b.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
        if ($t.Current.ToggleState -ne 'On') { 'tick the warning'; $t.Toggle() }
      }
    }
    foreach ($b in $inside) {
      if ($b.Current.Name -in 'OK', 'Connect', '&Connect', 'Yes', '&Yes') {
        "click '$($b.Current.Name)' in '$($c.Name)'"
        Click $b
        Start-Sleep -Milliseconds 500
      }
    }
  }
  $wins = $A::RootElement.FindAll($all, $any) | Where-Object { (Get-Process -Id $_.Current.ProcessId -ErrorAction SilentlyContinue).ProcessName -eq 'mstsc' }
  if ($i % 10 -eq 0) { "t=$i s: mstsc windows: $(($wins | ForEach-Object { "$($_.Current.ClassName)|$($_.Current.Name)" }) -join ' ; ')" }
  if ($wins | Where-Object { $_.Current.ClassName -eq 'RAIL_WINDOW' -and $_.Current.Name -match 'Notepad|Bloc' }) { "RemoteApp up at $i s"; exit 0 }
}
throw 'no RemoteApp window in 90 s'
