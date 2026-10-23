# Throwaway (spike/guide-v2-uia): the UIA tree of the windows of a program, by .NET's client, to look
# at what a window exposes (types and counts; names cut to 24 characters). Fixtures only, no user data.
param([string]$Process, [int]$Depth = 6)
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$A = [System.Windows.Automation.AutomationElement]
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
function Show($el, $d) {
  if ($d -gt $Depth) { return }
  $c = $el.Current
  $n = $c.Name; if ($n.Length -gt 24) { $n = $n.Substring(0, 24) + '...' }
  $r = $c.BoundingRectangle
  "{0}{1} '{2}' [{3},{4},{5},{6}] class={7} fw={8}" -f ('  ' * $d), $c.ControlType.ProgrammaticName, $n, [int]$r.X, [int]$r.Y, [int]$r.Width, [int]$r.Height, $c.ClassName, $c.FrameworkId
  $ch = $walker.GetFirstChild($el)
  while ($ch -ne $null) { Show $ch ($d + 1); $ch = $walker.GetNextSibling($ch) }
}
$pids = (Get-Process -Name $Process -ErrorAction SilentlyContinue).Id
"== $Process : pids $($pids -join ',')"
$cond = New-Object System.Windows.Automation.PropertyCondition($A::ProcessIdProperty, 0)
foreach ($p in $pids) {
  $cond = New-Object System.Windows.Automation.PropertyCondition($A::ProcessIdProperty, $p)
  foreach ($w in $A::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children, $cond)) {
    Show $w 0
    $all = $w.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    "   descendants: $($all.Count)"
  }
}
