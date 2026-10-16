# Helpers to drive a running Cura window from PowerShell: UI Automation for finding controls,
# mouse/keyboard for clicking, a window screenshot for looking. Dot-source it:  . scripts\cura_ui.ps1
# Qt Quick exposes names and rectangles, but not always working Invoke patterns - so clicks are
# real mouse clicks at the element's centre.
#
# Safety: clicks, keys and screenshots work on screen coordinates, so they refuse to run unless
# the window there belongs to Cura. Another app on top (the user's own work) stops the script
# instead of receiving the click, and never ends up in a screenshot.

Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Windows.Forms, System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class Native {
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, UIntPtr e);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(long point);
    [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr h, uint flags);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
}
"@
[Native]::SetProcessDPIAware() | Out-Null
$script:UIA = [System.Windows.Automation.AutomationElement]

# The newest Cura process (a second start while one runs leaves two).
function Get-CuraProcess {
    $p = Get-Process -Name "UltiMaker-Cura" -ErrorAction SilentlyContinue | Sort-Object StartTime -Descending | Select-Object -First 1
    if (-not $p) { throw "Cura is not running" }
    $p
}

# All top-level windows of a Cura process (main window, dialogs, the assistant).
function Get-CuraWindows([int]$ProcessId = 0) {
    if ($ProcessId -eq 0) { $ProcessId = (Get-CuraProcess).Id }
    $c = New-Object System.Windows.Automation.PropertyCondition($UIA::ProcessIdProperty, $ProcessId)
    $UIA::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children, $c)
}

# Bring a window to the front. Windows blocks SetForegroundWindow from a background process, so a
# tap on Alt first; the result is checked, not assumed.
function Activate-Window($element) {
    $h = [IntPtr]$element.Current.NativeWindowHandle
    [Native]::ShowWindow($h, 9) | Out-Null   # restore if minimised
    [Native]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero); [Native]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
    [Native]::SetForegroundWindow($h) | Out-Null
    Start-Sleep -Milliseconds 400
}

# The main window ("... - UltiMaker Cura"), brought to the front; throws if something else stays on top.
function Get-CuraWindow {
    $w = Get-CuraWindows | Where-Object { $_.Current.Name -match 'UltiMaker Cura$' } | Select-Object -First 1
    if (-not $w) { throw "no UI Automation main window for Cura" }
    Activate-Window $w
    Assert-CuraOnTop $w
    $w
}

function Get-AssistantWindow {
    Get-CuraWindows | Where-Object { $_.Current.Name -eq 'AI Assistant' } | Select-Object -First 1
}

# Name of the process that owns the window at a screen point.
function Get-ProcessAt([int]$x, [int]$y) {
    $point = ([long]$y -shl 32) -bor ([long]$x -band 0xFFFFFFFFL)
    $h = [Native]::WindowFromPoint($point)
    if ($h -eq [IntPtr]::Zero) { return "(nothing)" }
    $root = [Native]::GetAncestor($h, 2)
    $owner = [uint32]0; [Native]::GetWindowThreadProcessId($root, [ref]$owner) | Out-Null
    (Get-Process -Id $owner -ErrorAction SilentlyContinue).ProcessName
}

function Assert-OverCura([int]$x, [int]$y) {
    $name = Get-ProcessAt $x $y
    if ($name -ne "UltiMaker-Cura") { throw "Refusing to act at ${x},${y}: the window there belongs to '$name', not Cura" }
}

# Cura must be what the user sees at the centre and the corners of this window.
function Assert-CuraOnTop($element) {
    $r = $element.Current.BoundingRectangle
    $points = @(@(0.5, 0.5), @(0.1, 0.1), @(0.9, 0.1), @(0.1, 0.9), @(0.9, 0.9))
    foreach ($p in $points) { Assert-OverCura ([int]($r.X + $r.Width * $p[0])) ([int]($r.Y + $r.Height * $p[1])) }
}

# Elements under $scope whose name matches the regex (optionally of one control type, e.g. Button).
function Find-Elements($scope, [string]$NameRegex, [string]$Type = "") {
    $all = $scope.FindAll([System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($e in $all) {
        if ($e.Current.Name -match $NameRegex -and
            ($Type -eq "" -or $e.Current.ControlType.ProgrammaticName -eq "ControlType.$Type")) { $e }
    }
}

function Click-Element($e) {
    $r = $e.Current.BoundingRectangle
    Click-At ([int]($r.X + $r.Width / 2)) ([int]($r.Y + $r.Height / 2))
}

function Click-At([int]$x, [int]$y) {
    Assert-OverCura $x $y
    [Native]::SetCursorPos($x, $y) | Out-Null
    Start-Sleep -Milliseconds 120
    Assert-OverCura $x $y   # still Cura after the pointer arrived
    [Native]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)  # left down
    [Native]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)  # left up
    Start-Sleep -Milliseconds 400
}

# Keys go to the foreground window, so it must be Cura's.
function Assert-CuraForeground {
    $root = [Native]::GetAncestor([Native]::GetForegroundWindow(), 2)
    $owner = [uint32]0; [Native]::GetWindowThreadProcessId($root, [ref]$owner) | Out-Null
    $name = (Get-Process -Id $owner -ErrorAction SilentlyContinue).ProcessName
    if ($name -ne "UltiMaker-Cura") { throw "Refusing to send keys: the foreground window belongs to '$name', not Cura" }
}

# SendKeys syntax: plain text types itself; {ENTER}, {TAB}, ^v ... are keys. Escape + { } ( ) etc.
function Send-Text([string]$text) {
    Assert-CuraForeground
    $escaped = $text -replace '([{}()+^%~\[\]])', '{$1}'
    [System.Windows.Forms.SendKeys]::SendWait($escaped)
    Start-Sleep -Milliseconds 300
}
function Send-Keys([string]$keys) {
    Assert-CuraForeground
    [System.Windows.Forms.SendKeys]::SendWait($keys); Start-Sleep -Milliseconds 300
}

# Screenshot of one Cura window's rectangle; refused if anything else covers it.
function Save-Shot($element, [string]$path) {
    Assert-CuraOnTop $element
    $r = $element.Current.BoundingRectangle
    $bmp = New-Object System.Drawing.Bitmap([int]$r.Width, [int]$r.Height)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen([int]$r.X, [int]$r.Y, 0, 0, $bmp.Size)
    $g.Dispose(); $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
}

# Opens the assistant dialog the reliable way: the stand-in install (install_plugin.ps1
# -FakeClaude) watches for this file. Cura's menu bar does not react to synthetic input.
function Open-Assistant {
    $trigger = Join-Path (Split-Path $PSScriptRoot) ".work\open_assistant.trigger"
    Set-Content -Path $trigger -Value "open" -Encoding ascii
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 1
        $dlg = Get-AssistantWindow
        if ($dlg) { return $dlg }
    }
    throw "The assistant dialog did not open (is the stand-in install active and Cura restarted?)"
}

# Types `$text` into the assistant's input field and clicks the named button
# ("Send" = automatic routing, "Suggest settings", "Suggest orientation" or "Edit part or setup"),
# then waits for the answer.
# The field is not exposed by UI Automation, so it is clicked at its place in the dialog.
function Ask-Assistant($dlg, [string]$text, [string]$button = "Send", [int]$timeoutSeconds = 60) {
    Activate-Window $dlg  # another app may have come to the front since the last call
    $r = $dlg.Current.BoundingRectangle
    $bottom = @(Find-Elements $dlg "^$button$" | Where-Object { $_.Current.ControlType.ProgrammaticName -match 'Button' })[0]
    Click-At ([int]($r.X + 235)) ([int]($bottom.Current.BoundingRectangle.Y + 17))
    Send-Text $text
    Click-Element $bottom
    for ($i = 0; $i -lt $timeoutSeconds / 2; $i++) {
        Start-Sleep -Seconds 2
        if (-not (Find-Elements $dlg 'Thinking|Slicing|Test slicing')) { break }
    }
}
