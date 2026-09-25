# A demonstration you can run in front of people.
#
#     .\Demo_ForManagement.ps1
#
# Three different G-MES screens, one after another, each in its own window
# beside the browser. For every one the audience sees the same four things:
#
#     the questions  ->  the answers typed in  ->  G-MES working  ->  the file
#
# The three are deliberately different shapes, because "it works on the
# screen we built it for" proves nothing:
#
#     P1112UM00  Production Plan by Order(Line)   8 filters, a from/to pair
#     P1111UM00  Production Plan by Model         a differently NAMED to-date
#     M4151UM00  Work Calendar (a different module)  no filters, no dates
#
# What it does for each one:
#
#   1. Opens GMES_Workflow.bat in its own window, on the LEFT of the screen,
#      with the browser on the RIGHT.
#   2. Types the answers one character at a time, slowly enough to read.
#   3. Presses Enter and lets the work happen in view.
#   4. Saves a screenshot, closes the window, moves to the next.
#
# Why real keystrokes and not piped input: piped input is invisible. The
# point is that people see the values appear in the box.
#
# It changes nothing about how the tool works - it drives the same
# GMES_Workflow.bat a person would double-click.

[CmdletBinding()]
param(
    [int]   $TypeDelayMs = 55,      # per character - readable, not sluggish
    [int]   $ReadPauseMs = 1200,    # pause on each prompt so it can be read
    [switch]$SkipSignIn             # the browser is already up and signed in
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class DemoWin {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr h, int x, int y, int w, int t, bool repaint);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, System.Text.StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h);
}
"@

function Say($text)  { Write-Host "  $text" -ForegroundColor Cyan }
function Warn($text) { Write-Host "  $text" -ForegroundColor Yellow }

# The three runs. Blank dates are deliberate on the third - that screen has
# no date fields at all, and the tool has to notice rather than invent one.
$Runs = @(
    @{ Screen = "P1112UM00"; Division = "VD"; From = "20260909"; To = "20260909"
       Note = "8 filters, a from/to date pair" }
    @{ Screen = "P1111UM00"; Division = "VD"; From = "20260909"; To = "20260909"
       Note = "same idea, but its to-date column is named differently" }
    @{ Screen = "M4151UM00"; Division = "VD"; From = "";         To = ""
       Note = "a different module - no filters and no date fields at all" }
)

# --------------------------------------------------------------------------
# The browser, signed in before anyone is watching
# --------------------------------------------------------------------------
$cdpUp = $false
try {
    Invoke-WebRequest "http://127.0.0.1:9444/json/version" -TimeoutSec 3 -UseBasicParsing | Out-Null
    $cdpUp = $true
} catch { }

if (-not $cdpUp -and -not $SkipSignIn) {
    Say "The automation browser is not running. Starting it and signing in."
    Say "This takes up to a minute - Chrome will appear shortly."
    & python gmes_login.py            # output on purpose: silence looks like a hang
    if ($LASTEXITCODE -ne 0) {
        throw "Could not sign in to G-MES. Run 'python gmes_login.py --assist' " +
              "and sign in by hand once, then run this again."
    }
} elseif (-not $cdpUp) {
    throw "The automation browser is not running. Run 'python gmes_login.py' first."
} else {
    Say "Automation browser is already up and signed in."
}

$chrome = Get-CimInstance Win32_Process -Filter "name='chrome.exe'" |
          Where-Object { $_.CommandLine -like "*CDP Profile*" } |
          ForEach-Object { Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue } |
          Where-Object { $_.MainWindowHandle -ne 0 } |
          Select-Object -First 1
if (-not $chrome) { throw "The automation browser is not on screen. Run: python gmes_login.py" }

$area = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$half = [int]($area.Width / 2)
[DemoWin]::ShowWindow($chrome.MainWindowHandle, 9) | Out-Null      # restore if minimised
[DemoWin]::MoveWindow($chrome.MainWindowHandle, $area.X + $half, $area.Y,
                      $half, $area.Height, $true) | Out-Null
Say "G-MES is on the right of the screen."

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

# SendKeys treats + ^ % ~ ( ) { } [ ] as instructions rather than characters.
function Escape-Key([char]$c) {
    if ('+^%~(){}[]'.IndexOf($c) -ge 0) { return "{$c}" }
    return [string]$c
}

function Find-ToolWindow($title) {
    # Re-resolved every time, never cached. A handle captured when the window
    # first appears goes STALE: conhost's window is replaced as the hosted
    # command starts, so the handle we had was reported as "no longer a
    # window" eight seconds later and every focus attempt failed against it.
    #
    # This is the same rule the G-MES automation follows for Nexacro ids -
    # an identity that is regenerated is not an address (CLAUDE.md 3.4).
    $p = Get-Process | Where-Object {
        $_.MainWindowTitle -like "*$title*" -and $_.MainWindowHandle -ne 0
    } | Select-Object -First 1
    if ($p) { return $p.MainWindowHandle }
    return [IntPtr]::Zero
}

function Focus-Tool($hwnd) {
    # Windows refuses SetForegroundWindow from a process that has not
    # received input - which is every script launched from somewhere else.
    # Observed exactly that: the window opened, the call was ignored, and the
    # guard below stopped the demo rather than typing a screen code into
    # whatever the presenter had open.
    #
    # Tapping ALT is the documented way out: it gives this thread the input
    # state Windows requires before it will honour a foreground change. The
    # key goes nowhere - nothing has focus to receive it yet.
    for ($try = 0; $try -lt 10; $try++) {
        [DemoWin]::ShowWindow($hwnd, 9) | Out-Null                 # SW_RESTORE
        [DemoWin]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero)        # ALT down
        [DemoWin]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)        # ALT up
        [DemoWin]::SetForegroundWindow($hwnd) | Out-Null
        [DemoWin]::BringWindowToTop($hwnd) | Out-Null
        Start-Sleep -Milliseconds 300
        if ([DemoWin]::GetForegroundWindow() -eq $hwnd) { return $true }
    }
    return $false
}

function Describe-Window($hwnd) {
    if (-not [DemoWin]::IsWindow($hwnd)) { return "hwnd=$hwnd (no longer a window)" }
    $sb = New-Object System.Text.StringBuilder 300
    [DemoWin]::GetWindowText($hwnd, $sb, 300) | Out-Null
    return "hwnd=$hwnd title='$($sb.ToString())'"
}

function Type-Answer($title, [string]$text) {
    $hwnd = Find-ToolWindow $title
    if ($hwnd -eq [IntPtr]::Zero) {
        throw "The tool window '$title' has gone. Nothing was typed."
    }
    if (-not (Focus-Tool $hwnd)) {
        # Say what was actually seen. A guard that only refuses tells you it
        # went wrong; one that reports tells you why.
        throw ("Could not bring the tool window to the front, so nothing was " +
               "typed.`n      wanted : " + (Describe-Window $hwnd) +
               "`n      has focus: " + (Describe-Window ([DemoWin]::GetForegroundWindow())))
    }
    foreach ($c in $text.ToCharArray()) {
        [System.Windows.Forms.SendKeys]::SendWait((Escape-Key $c))
        Start-Sleep -Milliseconds $TypeDelayMs
    }
    Start-Sleep -Milliseconds 300
    [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
    Start-Sleep -Milliseconds $ReadPauseMs
}

function Start-ToolWindow($title) {
    # Through conhost.exe on purpose. Started as a plain cmd.exe, a console on
    # modern Windows opens inside Windows Terminal: the window belongs to
    # WindowsTerminal.exe (so the process's own handle stays 0), and it may
    # land as a TAB in a window already open, where bringing "it" to the front
    # shows whichever tab is selected. conhost gives a classic window, always.
    Start-Process -FilePath "conhost.exe" `
                  -ArgumentList "cmd.exe /c title $title && GMES_Workflow.bat" `
                  -WorkingDirectory $PSScriptRoot | Out-Null
    $found = $null
    for ($i = 0; $i -lt 150 -and -not $found; $i++) {
        Start-Sleep -Milliseconds 200
        $found = Get-Process | Where-Object {
            $_.MainWindowTitle -like "*$title*" -and $_.MainWindowHandle -ne 0
        } | Select-Object -First 1
    }
    if (-not $found) { throw "The tool window '$title' did not appear." }
    return $found
}

function Save-Screenshot($name) {
    $path = Join-Path $PSScriptRoot ("demo_" + $name + "_" +
            (Get-Date -Format "yyyyMMdd_HHmmss") + ".png")
    $bmp = New-Object System.Drawing.Bitmap($area.Width, $area.Height)
    $gfx = [System.Drawing.Graphics]::FromImage($bmp)
    $gfx.CopyFromScreen($area.X, $area.Y, 0, 0, $bmp.Size)
    $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    $gfx.Dispose(); $bmp.Dispose()
    return $path
}

# --------------------------------------------------------------------------
# The three runs
# --------------------------------------------------------------------------
$summary = @()
$number = 0

foreach ($run in $Runs) {
    $number++
    $title = "G-MES REPORT TOOL - $number of $($Runs.Count)"
    Write-Host ""
    Say "=== $number of $($Runs.Count): $($run.Screen) - $($run.Note) ==="

    Start-ToolWindow $title | Out-Null

    # The tool prints its banner and confirms the session before asking
    # anything. Typing early is harmless in any case - a Windows console
    # buffers keystrokes and hands them over when the program next reads -
    # so this wait is for appearance, not correctness.
    Start-Sleep -Seconds 8

    # The answers, in the order the questions are asked. A blank From date
    # means the To question is never reached, so it must not be answered.
    $answers = @($run.Screen, $run.Division)
    if ($run.From) { $answers += @($run.From, $run.To) } else { $answers += @("") }
    $answers += @("", "")            # no extra filter; then Enter to start

    # Placed only now, once the window has settled into its final identity.
    $hwnd = Find-ToolWindow $title
    if ($hwnd -ne [IntPtr]::Zero) {
        [DemoWin]::MoveWindow($hwnd, $area.X, $area.Y, $half, $area.Height, $true) | Out-Null
    }

    Say "Typing the answers..."
    foreach ($answer in $answers) { Type-Answer $title $answer }

    Say "Running - steps on the left, G-MES answering on the right."
    $startedAt = Get-Date
    $deadline = $startedAt.AddMinutes(3)
    $delivered = $null
    while ((Get-Date) -lt $deadline -and -not $delivered) {
        Start-Sleep -Seconds 2
        $delivered = Get-ChildItem "Data Hub Folder\GMES" -Filter *.csv -ErrorAction SilentlyContinue |
                     Where-Object { $_.LastWriteTime -gt $startedAt } |
                     Sort-Object LastWriteTime -Descending | Select-Object -First 1
    }

    Start-Sleep -Seconds 2
    $shot = Save-Screenshot $run.Screen

    if ($delivered) {
        Say "Delivered: $($delivered.Name)"
        $summary += [pscustomobject]@{ Screen = $run.Screen; Result = "ok"
                                       File = $delivered.Name; Shot = $shot }
    } else {
        Warn "No file appeared for $($run.Screen) - leaving the window open to read."
        $summary += [pscustomobject]@{ Screen = $run.Screen; Result = "FAILED"
                                       File = "-"; Shot = $shot }
    }

    # The tool is sitting on "Press Enter to close..." - close it and move on,
    # except when it failed, where the message on screen is the whole point.
    if ($delivered) {
        $h2 = Find-ToolWindow $title
        if ($h2 -ne [IntPtr]::Zero -and (Focus-Tool $h2)) { [System.Windows.Forms.SendKeys]::SendWait("{ENTER}") }
        Start-Sleep -Seconds 2
    }
}

Write-Host ""
Say "================ DEMONSTRATION COMPLETE ================"
$summary | Format-Table -AutoSize
Say "Screenshots and files are in: $PSScriptRoot"
Write-Host ""
