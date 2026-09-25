package tools

import (
	"encoding/binary"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
	"unicode/utf16"

	"github.com/shirou/gopsutil/v4/disk"

	"github.com/coolman1984/performance/internal/core"
	"github.com/coolman1984/performance/internal/fix"
	"github.com/coolman1984/performance/internal/kb"
	"github.com/coolman1984/performance/internal/sys"
)

func init() {
	core.Register(&core.Tool{Name: "missing", Category: "repair", InBrief: true,
		Short:    "Things that should exist but don't: core services, system files, PATH, user folders, runtimes, Store, winget",
		Aliases:  []string{"essentials"},
		Keywords: []string{"missing", "broken", "deleted", "disabled", "gone", "should", "ناقص", "ناقصة", "اتمسح", "اتمسحت", "مش موجود", "اختفى", "بايظ"},
		Run:      runMissing})
	core.Register(&core.Tool{Name: "shortcuts", Category: "repair", InBrief: true,
		Short:    "Broken shortcuts on the Desktop, Start menu and taskbar",
		Aliases:  []string{"lnk"},
		Keywords: []string{"shortcut", "shortcuts", "icon", "icons", "start menu", "اختصار", "اختصارات", "ايقونات"},
		Run:      runShortcuts})
	core.Register(&core.Tool{Name: "path", Category: "repair", InBrief: true,
		Short:    "PATH variable audit: dead folders, duplicates, missing Windows entries",
		Keywords: []string{"path", "environment", "variable", "command not found", "not recognized", "متغيرات"},
		Run:      runPath})
	core.Register(&core.Tool{Name: "orphans", Category: "repair", InBrief: true, Slow: true,
		Short:    "Leftovers of uninstalled programs, broken uninstall entries, recently installed apps",
		Aliases:  []string{"leftovers", "programs", "installed"},
		Keywords: []string{"uninstall", "leftover", "leftovers", "orphan", "installed", "programs", "بقايا", "برامج", "متسطبة", "حذف"},
		Run:      runOrphans})
	core.Register(&core.Tool{Name: "recover", Category: "repair", InBrief: true,
		Short:    "Find things deleted by mistake: Recycle Bin (with original paths), restore points, File History, OneDrive",
		Params:   []core.Param{{Name: "find", Desc: "only show deleted items whose name/path contains this text"}},
		Aliases:  []string{"undelete", "restore"},
		Keywords: []string{"deleted", "recover", "restore", "undelete", "lost", "mistake", "recycle", "اتمسح", "رجع", "استرجاع", "مسحت", "بالغلط", "سلة"},
		Run:      runRecover})
}

type missingPS struct {
	Services sys.List[struct {
		Name   string
		Exists bool
		Status string
		Start  int
	}]
	Folders                     sys.List[struct{ Name, Raw, Path string }]
	Store, Winget, AppInstaller bool
	NetFx, VCx64, VCx86         int
	WebView2                    string
	MachinePath                 string
}

const missingScript = `
$o = [ordered]@{}
$o.Services = @(foreach($n in NAMES){ $s = Get-Service -Name $n -ErrorAction SilentlyContinue
  $st = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\$n" -Name Start).Start
  [pscustomobject]@{ Name=$n; Exists=[bool]$s; Status=[string]$s.Status; Start=[int]$st } })
$usf = Get-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders'
$o.Folders = @(foreach($n in $usf.GetValueNames()){ $raw = [string]$usf.GetValue($n, $null, 'DoNotExpandEnvironmentNames'); [pscustomobject]@{ Name=$n; Raw=$raw; Path=[Environment]::ExpandEnvironmentVariables($raw) } })
$o.Store = [bool](Get-AppxPackage -Name Microsoft.WindowsStore)
$o.AppInstaller = [bool](Get-AppxPackage -Name Microsoft.DesktopAppInstaller)
$o.Winget = [bool](Get-Command winget.exe -ErrorAction SilentlyContinue)
$o.NetFx = [int](Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full' -Name Release).Release
$o.VCx64 = [int](Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64' -Name Installed).Installed
$o.VCx86 = [int](Get-ItemProperty 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x86' -Name Installed).Installed
$wv = 'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
$o.WebView2 = [string](Get-ItemProperty "HKLM:\SOFTWARE\WOW6432Node\$wv" -Name pv).pv
if(-not $o.WebView2){ $o.WebView2 = [string](Get-ItemProperty "HKCU:\Software\$wv" -Name pv).pv }
$o.MachinePath = [string](Get-Item 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment').GetValue('Path', $null, 'DoNotExpandEnvironmentNames')
$o
`

var shellFolderDefaults = map[string][2]string{
	"Desktop":                                {"Desktop", `%USERPROFILE%\Desktop`},
	"Personal":                               {"Documents", `%USERPROFILE%\Documents`},
	"{374DE290-123F-4565-9164-39C4925E467B}": {"Downloads", `%USERPROFILE%\Downloads`},
	"My Pictures":                            {"Pictures", `%USERPROFILE%\Pictures`},
	"My Music":                               {"Music", `%USERPROFILE%\Music`},
	"My Video":                               {"Videos", `%USERPROFILE%\Videos`},
	"AppData":                                {"AppData (Roaming)", `%USERPROFILE%\AppData\Roaming`},
	"Local AppData":                          {"AppData (Local)", `%USERPROFILE%\AppData\Local`},
}

func runMissing(c *core.Ctx) (*core.Result, error) {
	r := &core.Result{Title: "Missing or disabled essentials"}
	k := sys.Known()
	t := core.Table{Headers: []string{"Check", "Status"}}
	okCount := 0

	// critical files: pure Go, works without PowerShell
	var missingFiles []string
	for _, f := range kb.CriticalFiles {
		p := filepath.Join(k.Windows, f)
		if (f == `System32\winload.exe` || f == `System32\winload.efi`) && (sys.Exists(filepath.Join(k.Windows, `System32\winload.exe`)) || sys.Exists(filepath.Join(k.Windows, `System32\winload.efi`))) {
			continue
		}
		if sys.IsWindows && !sys.Exists(p) {
			missingFiles = append(missingFiles, p)
		}
	}
	repair := []core.Fix{
		{ID: "dism", Title: "Restore Windows files from Windows Update (DISM)", Risk: core.Safe, Admin: true,
			Action: core.Action{Kind: core.ActExec, Cmd: []string{"Dism.exe", "/Online", "/Cleanup-Image", "/RestoreHealth"}}},
		{ID: "sfc", Title: "Then repair system files (sfc /scannow)", Risk: core.Safe, Admin: true,
			Action: core.Action{Kind: core.ActExec, Cmd: []string{"sfc", "/scannow"}}},
	}
	if len(missingFiles) > 0 {
		r.Add(core.Finding{ID: "system-files", Severity: core.Critical, Title: fmt.Sprintf("%d Windows system files are missing", len(missingFiles)),
			Detail: "Deleted by a 'cleaner', malware or disk damage. DISM re-downloads the originals and sfc puts them back.", Evidence: missingFiles, Fixes: repair})
	} else if sys.IsWindows {
		okCount++
		t.Rows = append(t.Rows, []string{"Core system files", "all present"})
	}

	// environment
	tmp := os.Getenv("TEMP")
	if tmp == "" || !sys.Exists(tmp) {
		target := filepath.Join(k.Local, "Temp")
		r.Add(core.Finding{ID: "temp-dir", Severity: core.High, Title: "The TEMP folder is missing: " + tmp,
			Detail: "Installers and many apps fail with strange errors when TEMP doesn't exist.",
			Fixes: []core.Fix{{ID: "create", Title: "Recreate " + target, Risk: core.Safe,
				Action: core.Action{Kind: core.ActPS, Script: "New-Item -ItemType Directory -Force -Path " + sys.PSQuote(target) + "\n[Environment]::SetEnvironmentVariable('TEMP'," + sys.PSQuote(target) + ",'User')\n[Environment]::SetEnvironmentVariable('TMP'," + sys.PSQuote(target) + ",'User')"}}}})
	}
	if cs := os.Getenv("ComSpec"); sys.IsWindows && (cs == "" || !sys.Exists(cs)) {
		r.Add(core.Finding{ID: "comspec", Severity: core.High, Title: "ComSpec does not point to cmd.exe (" + cs + ")", Detail: "Batch files and many installers break.",
			Fixes: []core.Fix{{ID: "reset", Title: "Reset ComSpec", Risk: core.Safe, Admin: true,
				Action: core.Action{Kind: core.ActPS, Script: `[Environment]::SetEnvironmentVariable('ComSpec', '%SystemRoot%\system32\cmd.exe', 'Machine')`}}}})
	}

	var m missingPS
	names := make([]string, len(kb.EssentialServices))
	for i, s := range kb.EssentialServices {
		names[i] = s.Name
	}
	err := sys.PSJSON(c, strings.Replace(missingScript, "NAMES", sys.PSArray(names), 1), &m)
	if windowsOnly(r, err) || err != nil {
		r.Tables = append(r.Tables, t)
		return r, nil
	}
	type svcState struct {
		exists bool
		status string
		start  int
	}
	state := map[string]svcState{}
	for _, s := range m.Services {
		state[s.Name] = svcState{s.Exists, s.Status, s.Start}
	}
	svcOK := 0
	for _, es := range kb.EssentialServices {
		st := state[es.Name]
		switch {
		case !st.exists:
			r.Add(core.Finding{ID: "svc-" + slug(es.Name) + "-gone", Severity: core.Critical, Title: "Windows service is missing: " + es.Title + " (" + es.Name + ")",
				Detail: es.Why + " A missing core service usually means a 'debloat' script or malware removed it.", Fixes: repair})
		case st.start == 4:
			r.Add(core.Finding{ID: "svc-" + slug(es.Name) + "-disabled", Severity: core.High, Title: "Essential service is disabled: " + es.Title,
				Detail: es.Why,
				Fixes: []core.Fix{{ID: "enable", Title: "Re-enable " + es.Title, Risk: core.Safe, Admin: true, Reversible: true,
					Action: core.Action{Kind: core.ActPS, Script: serviceStartScript(es.Name, es.Start, map[bool]string{true: "start"}[es.Run])},
					Undo:   &core.Action{Kind: core.ActPS, Script: serviceStartScript(es.Name, 4, "")}}}})
		case es.Run && !strings.EqualFold(st.status, "Running"):
			r.Add(core.Finding{ID: "svc-" + slug(es.Name) + "-stopped", Severity: core.Medium, Title: "Essential service is not running: " + es.Title,
				Detail: es.Why,
				Fixes: []core.Fix{{ID: "start", Title: "Start " + es.Title, Risk: core.Safe, Admin: true,
					Action: core.Action{Kind: core.ActPS, Script: "Start-Service -Name " + sys.PSQuote(es.Name)}}}})
		default:
			svcOK++
		}
	}
	t.Rows = append(t.Rows, []string{"Essential services", fmt.Sprintf("%d/%d healthy", svcOK, len(kb.EssentialServices))})

	for _, f := range m.Folders {
		def, ok := shellFolderDefaults[f.Name]
		if !ok {
			continue
		}
		if sys.Exists(f.Path) {
			okCount++
			continue
		}
		key := `HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders`
		r.Add(core.Finding{ID: "folder-" + slug(def[0]), Severity: core.High, Title: fmt.Sprintf("Your %s folder points to a place that doesn't exist", def[0]),
			Detail:   "Often left behind by OneDrive backup being turned off, or a drive that was removed. Apps then fail to save files.",
			Evidence: []string{f.Raw},
			Fixes: []core.Fix{{ID: "reset", Title: "Point " + def[0] + " back to " + def[1], Risk: core.Moderate, Reversible: true,
				Action: core.Action{Kind: core.ActPS, Script: fmt.Sprintf("New-Item -ItemType Directory -Force -Path ([Environment]::ExpandEnvironmentVariables(%s)) | Out-Null\n%s\nStop-Process -Name explorer -Force", sys.PSQuote(def[1]), regSetScript(key, f.Name, "ExpandString", def[1]))},
				Undo:   &core.Action{Kind: core.ActPS, Script: regSetScript(key, f.Name, "ExpandString", f.Raw)}}}})
	}

	// machine PATH must contain the Windows folders
	mp := strings.ToLower(sys.Expand(strings.ReplaceAll(m.MachinePath, "%SystemRoot%", k.Windows)))
	var need []string
	for _, e := range []string{`%SystemRoot%\system32`, `%SystemRoot%`, `%SystemRoot%\System32\Wbem`, `%SystemRoot%\System32\WindowsPowerShell\v1.0\`} {
		exp := strings.ToLower(strings.TrimRight(strings.ReplaceAll(e, "%SystemRoot%", k.Windows), `\`))
		found := false
		for _, part := range strings.Split(mp, ";") {
			if strings.TrimRight(strings.TrimSpace(part), `\`) == exp {
				found = true
			}
		}
		if !found {
			need = append(need, e)
		}
	}
	if len(need) > 0 && m.MachinePath != "" {
		newPath := strings.Join(need, ";") + ";" + m.MachinePath
		key := `HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment`
		r.Add(core.Finding{ID: "path-windows", Severity: core.High, Title: "System PATH is missing Windows folders", Evidence: need,
			Detail: "Commands like ipconfig, powershell or where 'are not recognized'. Usually caused by an installer that overwrote PATH.",
			Fixes: []core.Fix{{ID: "restore", Title: "Put the Windows folders back at the front of PATH", Risk: core.Safe, Admin: true, Reversible: true,
				Action: core.Action{Kind: core.ActPS, Script: regSetScript(key, "Path", "ExpandString", newPath)},
				Undo:   &core.Action{Kind: core.ActPS, Script: regSetScript(key, "Path", "ExpandString", m.MachinePath)}}}})
	} else {
		okCount++
	}

	t.Rows = append(t.Rows, []string{"Microsoft Store", yesNo(m.Store)}, []string{"winget (App Installer)", yesNo(m.Winget)},
		[]string{".NET Framework 4.8", yesNo(m.NetFx >= 528040)}, []string{"Visual C++ 2015-2022 x64", yesNo(m.VCx64 == 1)},
		[]string{"Visual C++ 2015-2022 x86", yesNo(m.VCx86 == 1)}, []string{"Edge WebView2 runtime", nonEmpty(m.WebView2, "no")})
	if !m.Store {
		r.Add(core.Finding{ID: "store", Severity: core.Medium, Title: "Microsoft Store is missing", Detail: "Store apps can't be installed or updated, and some Windows features rely on it.",
			Fixes: []core.Fix{{ID: "reinstall", Title: "Reinstall the Store (wsreset -i)", Risk: core.Safe, Action: core.Action{Kind: core.ActExec, Cmd: []string{"wsreset.exe", "-i"}}}}})
	}
	if !m.Winget {
		f := core.Finding{ID: "winget", Severity: core.Low, Title: "winget (Windows package manager) is not available",
			Detail: "winget installs and updates apps (and runtimes below) with one command. It comes with App Installer."}
		if m.AppInstaller {
			f.Fixes = []core.Fix{{ID: "register", Title: "Re-register App Installer", Risk: core.Safe,
				Action: core.Action{Kind: core.ActPS, Script: "Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.DesktopAppInstaller_8wekyb3d8bbwe"}}}
		} else {
			f.Fixes = []core.Fix{{ID: "install", Title: "Install App Installer from the Store", Risk: core.Safe,
				Action: core.Action{Kind: core.ActExec, Cmd: []string{"cmd", "/c", "start", "ms-windows-store://pdp/?ProductId=9NBLGGH4NNS1"}}}}
		}
		r.Add(f)
	}
	winget := func(id string) core.Action {
		return core.Action{Kind: core.ActExec, Cmd: []string{"winget", "install", "--id", id, "-e", "--silent", "--accept-package-agreements", "--accept-source-agreements"}}
	}
	if m.VCx64 != 1 || m.VCx86 != 1 {
		f := core.Finding{ID: "vcredist", Severity: core.Medium, Title: "Visual C++ runtime is missing",
			Detail: "Games and many apps fail with 'MSVCP140.dll / VCRUNTIME140.dll was not found' without it."}
		if m.VCx64 != 1 {
			f.Fixes = append(f.Fixes, core.Fix{ID: "x64", Title: "Install VC++ 2015-2022 x64", Risk: core.Safe, Admin: true, Action: winget("Microsoft.VCRedist.2015+.x64")})
		}
		if m.VCx86 != 1 {
			f.Fixes = append(f.Fixes, core.Fix{ID: "x86", Title: "Install VC++ 2015-2022 x86", Risk: core.Safe, Admin: true, Action: winget("Microsoft.VCRedist.2015+.x86")})
		}
		r.Add(f)
	}
	if m.WebView2 == "" {
		r.Add(core.Finding{ID: "webview2", Severity: core.Low, Title: "Edge WebView2 runtime is missing",
			Detail: "New Outlook, Teams, Widgets and many modern apps show blank windows without it.",
			Fixes:  []core.Fix{{ID: "install", Title: "Install WebView2 runtime", Risk: core.Safe, Admin: true, Action: winget("Microsoft.EdgeWebView2Runtime")}}})
	}
	if m.NetFx > 0 && m.NetFx < 528040 {
		r.Add(core.Finding{ID: "netfx", Severity: core.Low, Title: ".NET Framework is older than 4.8",
			Fixes: []core.Fix{{ID: "install", Title: "Install .NET Framework 4.8.1", Risk: core.Safe, Admin: true, Action: winget("Microsoft.DotNet.Framework.DeveloperPack_4")}}})
	}
	r.Tables = append(r.Tables, t)
	r.Summary = fmt.Sprintf("%d things missing, disabled or broken.", len(r.Findings))
	return r, nil
}

func yesNo(b bool) string {
	if b {
		return "yes"
	}
	return "MISSING"
}

const shortcutsScript = `
$sh = New-Object -ComObject WScript.Shell
$dirs = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('CommonDesktopDirectory'), [Environment]::GetFolderPath('StartMenu'),
  [Environment]::GetFolderPath('CommonStartMenu'), "$env:APPDATA\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar")
@{ items = @(foreach($d in $dirs){ if(-not $d -or -not (Test-Path -LiteralPath $d)){ continue }
  Get-ChildItem -LiteralPath $d -Filter *.lnk -Recurse -File -Force | ForEach-Object { $s = $sh.CreateShortcut($_.FullName); [pscustomobject]@{ Path=$_.FullName; Target=[string]$s.TargetPath } } }) }
`

func runShortcuts(c *core.Ctx) (*core.Result, error) {
	r := &core.Result{Title: "Broken shortcuts"}
	var out struct {
		Items sys.List[struct{ Path, Target string }]
	}
	if err := sys.PSJSON(c, shortcutsScript, &out); windowsOnly(r, err) || err != nil {
		return r, nil
	}
	var user, common []string
	var ev []string
	for _, s := range out.Items {
		tgt := sys.Expand(s.Target)
		if tgt == "" || strings.HasPrefix(tgt, `\\`) || strings.Contains(tgt, "://") || strings.HasPrefix(tgt, "::") {
			continue
		}
		if vol := filepath.VolumeName(tgt); vol != "" && !sys.Exists(vol+`\`) {
			continue // target on a drive that isn't connected right now
		}
		if sys.Exists(tgt) {
			continue
		}
		ev = append(ev, s.Path+" → "+s.Target)
		if strings.HasPrefix(strings.ToLower(s.Path), strings.ToLower(sys.Known().ProgramData)) || strings.Contains(strings.ToLower(s.Path), `\public\`) {
			common = append(common, s.Path)
		} else {
			user = append(user, s.Path)
		}
	}
	if len(user) > 0 {
		r.Add(core.Finding{ID: "user", Severity: core.Low, Title: fmt.Sprintf("%d broken shortcuts (yours)", len(user)), Evidence: filterEv(ev, user),
			Detail: "They point to programs or files that were uninstalled, moved or deleted.",
			Fixes: []core.Fix{{ID: "quarantine", Title: "Move them to the WinSight quarantine", Risk: core.Safe, Reversible: true,
				Action: core.Action{Kind: core.ActQuarantine, Paths: user}}}})
	}
	if len(common) > 0 {
		r.Add(core.Finding{ID: "common", Severity: core.Low, Title: fmt.Sprintf("%d broken shortcuts (all users)", len(common)), Evidence: filterEv(ev, common),
			Fixes: []core.Fix{{ID: "quarantine", Title: "Move them to the WinSight quarantine", Risk: core.Safe, Admin: true, Reversible: true,
				Action: core.Action{Kind: core.ActQuarantine, Paths: common}}}})
	}
	r.Summary = fmt.Sprintf("%d shortcuts checked, %d broken.", len(out.Items), len(user)+len(common))
	return r, nil
}

func filterEv(ev, paths []string) []string {
	var out []string
	for _, e := range ev {
		for _, p := range paths {
			if strings.HasPrefix(e, p+" → ") {
				out = append(out, e)
			}
		}
	}
	return out
}

// pathAudit returns the cleaned list, and the dead and duplicate entries.
func pathAudit(raw string) (clean, dead, dups []string) {
	seen := map[string]bool{}
	for _, e := range strings.Split(raw, ";") {
		e = strings.TrimSpace(e)
		if e == "" {
			continue
		}
		norm := strings.ToLower(strings.TrimRight(sys.Expand(e), `\/`))
		switch {
		case seen[norm]:
			dups = append(dups, e)
		case !sys.Exists(sys.Expand(e)):
			dead = append(dead, e)
		default:
			seen[norm] = true
			clean = append(clean, e)
		}
	}
	return
}

func runPath(c *core.Ctx) (*core.Result, error) {
	r := &core.Result{Title: "PATH audit"}
	var p struct{ User, Machine string }
	err := sys.PSJSON(c, `@{ User = [string](Get-Item 'HKCU:\Environment').GetValue('Path',$null,'DoNotExpandEnvironmentNames'); Machine = [string](Get-Item 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment').GetValue('Path',$null,'DoNotExpandEnvironmentNames') }`, &p)
	if windowsOnly(r, err) || err != nil {
		p.User = os.Getenv("PATH")
		if !sys.IsWindows {
			r.Summary = ""
		}
	}
	t := core.Table{Headers: []string{"Scope", "Entries", "Dead", "Duplicates", "Length"}}
	for _, scope := range []struct {
		name, raw, key string
		admin          bool
	}{{"User", p.User, `HKCU:\Environment`, false}, {"Machine", p.Machine, `HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment`, true}} {
		if scope.raw == "" {
			continue
		}
		clean, dead, dups := pathAudit(scope.raw)
		t.Rows = append(t.Rows, []string{scope.name, fmt.Sprint(len(clean) + len(dead) + len(dups)), fmt.Sprint(len(dead)), fmt.Sprint(len(dups)), fmt.Sprint(len(scope.raw))})
		if len(dead)+len(dups) == 0 {
			continue
		}
		ev := append(prefixAll("dead: ", dead), prefixAll("duplicate: ", dups)...)
		f := core.Finding{ID: strings.ToLower(scope.name), Severity: core.Low, Title: fmt.Sprintf("%s PATH has %d dead and %d duplicate entries", scope.name, len(dead), len(dups)),
			Detail: "Dead entries slow down every command lookup and hide real problems; duplicates can make the wrong version of a tool win.", Evidence: ev}
		if sys.IsWindows {
			f.Fixes = []core.Fix{{ID: "tidy", Title: "Remove dead and duplicate entries", Risk: core.Moderate, Admin: scope.admin, Reversible: true,
				Action: core.Action{Kind: core.ActPS, Script: regSetScript(scope.key, "Path", "ExpandString", strings.Join(clean, ";"))},
				Undo:   &core.Action{Kind: core.ActPS, Script: regSetScript(scope.key, "Path", "ExpandString", scope.raw)}}}
		}
		r.Add(f)
		if len(scope.raw) > 2047 {
			r.Add(core.Finding{ID: strings.ToLower(scope.name) + "-long", Severity: core.Medium, Title: scope.name + " PATH is longer than 2047 characters",
				Detail: "Some installers and the old setx tool silently truncate it, deleting entries."})
		}
	}
	r.Tables = append(r.Tables, t)
	r.Summary = fmt.Sprintf("%d PATH problems.", len(r.Findings))
	r.Notes = append(r.Notes, "Open a new terminal after fixing PATH; running apps keep the old value.")
	return r, nil
}

func prefixAll(p string, items []string) []string {
	out := make([]string, len(items))
	for i, s := range items {
		out[i] = p + s
	}
	return out
}

type program struct {
	Key, Name, Publisher, Version, Location, Uninstall, Date string
	SizeKB                                                   int64
}

const programsScript = `
$keys = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*','HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*'
@{ items = @(foreach($k in $keys){ Get-ItemProperty $k | Where-Object { $_.DisplayName -and -not $_.SystemComponent -and -not $_.ParentKeyName } | ForEach-Object {
  [pscustomobject]@{ Key=($_.PSPath -replace '^Microsoft\.PowerShell\.Core\\Registry::',''); Name=[string]$_.DisplayName; Publisher=[string]$_.Publisher; Version=[string]$_.DisplayVersion;
    Location=[string]$_.InstallLocation; Uninstall=[string]$_.UninstallString; Date=[string]$_.InstallDate; SizeKB=[int64]$_.EstimatedSize } } }) }
`

var systemDirs = map[string]bool{"microsoft": true, "windows": true, "common files": true, "windowsapps": true, "packages": true, "temp": true, "microsoft.net": true,
	"internet explorer": true, "windows defender": true, "windows mail": true, "windows media player": true, "windows nt": true, "windows photo viewer": true,
	"windows portable devices": true, "windows security": true, "windowspowershell": true, "modifiableswindowsapps": true, "reference assemblies": true,
	"msbuild": true, "dotnet": true, "package cache": true, "nuget": true, "npm": true, "npm-cache": true, "pip": true, "programs": true, "d3dscache": true,
	"crashdumps": true, "connecteddevicesplatform": true, "comms": true, "publishers": true, "virtualstore": true, "ssh": true, "application data": true,
	"history": true, "mozilla": true, "google": true, "jetbrains": true, "docker": true, "nvidia": true, "nvidia corporation": true, "amd": true, "intel": true,
	"winsight": true, "desktop.ini": true, "ms-playwright": true, "wsl": true, "regid.1991-06.com.microsoft": true, "ssoprovider": true, "usoshared": true,
	"usoprivate": true, "softwaredistribution": true, "chocolatey": true, "scoop": true, "uv": true, "go-build": true, "yarn": true, "pnpm": true}

func normName(s string) string {
	var b strings.Builder
	for _, r := range strings.ToLower(s) {
		if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') {
			b.WriteRune(r)
		}
	}
	return b.String()
}

func runOrphans(c *core.Ctx) (*core.Result, error) {
	r := &core.Result{Title: "Installed programs & leftovers"}
	var out struct{ Items sys.List[program] }
	err := sys.PSJSON(c, programsScript, &out)
	if windowsOnly(r, err) || err != nil {
		return r, nil
	}
	progs := []program(out.Items)
	// broken uninstall entries
	for _, p := range progs {
		exe, missing := targetMissing(p.Uninstall)
		if !missing || strings.Contains(strings.ToLower(p.Uninstall), "msiexec") {
			continue
		}
		backup := filepath.Join(fix.Dir(), "regbackup", slug(p.Name)+".reg")
		admin := strings.HasPrefix(p.Key, "HKEY_LOCAL_MACHINE")
		r.Add(core.Finding{ID: "entry-" + slug(p.Name), Severity: core.Low, Title: "Uninstall entry for a program that is gone: " + p.Name,
			Detail:   "It shows in Settings › Apps but its uninstaller no longer exists, so 'Uninstall' fails.",
			Evidence: []string{p.Key, "missing: " + exe},
			Fixes: []core.Fix{{ID: "remove", Title: "Remove the dead entry (registry backup kept)", Risk: core.Safe, Admin: admin, Reversible: true,
				Action: core.Action{Kind: core.ActPS, Script: fmt.Sprintf("New-Item -ItemType Directory -Force -Path %s | Out-Null\nreg export %s %s /y | Out-Null\nRemove-Item -Path %s -Recurse -Force",
					sys.PSQuote(filepath.Dir(backup)), sys.PSQuote(p.Key), sys.PSQuote(backup), sys.PSQuote("Registry::"+p.Key))},
				Undo: &core.Action{Kind: core.ActExec, Cmd: []string{"reg", "import", backup}}}}})
	}
	// recently installed — the first suspect when "it got slow last week"
	sort.Slice(progs, func(i, j int) bool { return progs[i].Date > progs[j].Date })
	tr := core.Table{Title: "Recently installed", Headers: []string{"Date", "Program", "Publisher", "Size"}}
	for _, p := range progs {
		if len(tr.Rows) >= 12 || len(p.Date) != 8 {
			continue
		}
		tr.Rows = append(tr.Rows, []string{p.Date[:4] + "-" + p.Date[4:6] + "-" + p.Date[6:], p.Name, p.Publisher, sys.HumanBytes(p.SizeKB << 10)})
	}
	r.Tables = append(r.Tables, tr)

	// leftovers: big folders that match no installed program
	known := make([]string, 0, len(progs)*2)
	for _, p := range progs {
		for _, s := range []string{p.Name, p.Publisher, filepath.Base(strings.TrimRight(p.Location, `\`))} {
			if n := normName(s); len(n) >= 3 {
				known = append(known, n)
			}
		}
	}
	matches := func(dir string) bool {
		n := normName(dir)
		if len(n) < 3 || systemDirs[strings.ToLower(dir)] {
			return true
		}
		for _, k := range known {
			if strings.Contains(k, n) || strings.Contains(n, k) {
				return true
			}
		}
		return false
	}
	k := sys.Known()
	tl := core.Table{Title: "Possible leftovers (no matching installed program)", Headers: []string{"Size", "Folder"}}
	var leftovers []sys.SizedPath
	for _, root := range []string{k.ProgramFiles, k.ProgramFilesX86, k.ProgramData, k.Local, k.Roaming} {
		entries, err := os.ReadDir(root)
		if err != nil {
			continue
		}
		c.Progress("checking %s", root)
		for _, e := range entries {
			if !e.IsDir() || matches(e.Name()) {
				continue
			}
			p := filepath.Join(root, e.Name())
			b, _ := sys.DirSize(c, p)
			if b >= 200<<20 {
				leftovers = append(leftovers, sys.SizedPath{Path: p, Bytes: b})
			}
		}
	}
	sort.Slice(leftovers, func(i, j int) bool { return leftovers[i].Bytes > leftovers[j].Bytes })
	for _, l := range leftovers {
		tl.Rows = append(tl.Rows, []string{sys.HumanBytes(l.Bytes), l.Path})
		r.Add(core.Finding{ID: "leftover-" + shortHash(l.Path), Severity: core.Info, Title: "Possible leftover: " + filepath.Base(l.Path),
			Bytes: l.Bytes, Evidence: []string{l.Path},
			Detail: "No installed program matches this folder name. It may belong to an uninstalled app — or to a portable/game launcher. Check before removing.",
			Fixes: []core.Fix{{ID: "recycle", Title: "Send the folder to the Recycle Bin", Risk: core.Risky, Reversible: true,
				Admin:  !strings.HasPrefix(strings.ToLower(l.Path), strings.ToLower(k.User)),
				Action: core.Action{Kind: core.ActPS, Script: recycleScript([]string{l.Path})}}}})
	}
	if len(tl.Rows) > 0 {
		r.Tables = append(r.Tables, tl)
	}
	r.Data = progs
	r.Summary = fmt.Sprintf("%d programs installed; %d possible leftovers (%s).", len(progs), len(leftovers), sys.HumanBytes(r.ReclaimableBytes()))
	return r, nil
}

// binEntry is one item in the Recycle Bin, decoded from its $I file.
type binEntry struct {
	Original string    `json:"original_path"`
	Deleted  time.Time `json:"deleted_at"`
	Size     int64     `json:"bytes"`
	Stored   string    `json:"stored_as"`
	Index    string    `json:"index_file"`
}

// parseIFile decodes a Recycle Bin $I metadata file (Vista+ v1 and Win10+ v2).
func parseIFile(b []byte) (orig string, size int64, deleted time.Time, ok bool) {
	if len(b) < 24 {
		return
	}
	ver := binary.LittleEndian.Uint64(b[0:])
	size = int64(binary.LittleEndian.Uint64(b[8:]))
	ft := int64(binary.LittleEndian.Uint64(b[16:]))
	deleted = time.Unix(0, (ft-116444736000000000)*100)
	var raw []byte
	switch ver {
	case 1:
		if len(b) < 24+520 {
			return
		}
		raw = b[24 : 24+520]
	case 2:
		if len(b) < 28 {
			return
		}
		n := int(binary.LittleEndian.Uint32(b[24:]))
		if len(b) < 28+n*2 {
			return
		}
		raw = b[28 : 28+n*2]
	default:
		return
	}
	u := make([]uint16, 0, len(raw)/2)
	for i := 0; i+1 < len(raw); i += 2 {
		v := binary.LittleEndian.Uint16(raw[i:])
		if v == 0 {
			break
		}
		u = append(u, v)
	}
	return string(utf16.Decode(u)), size, deleted, true
}

func readRecycleBin(c *core.Ctx) []binEntry {
	var roots []string
	parts, _ := disk.PartitionsWithContext(c, false)
	for _, p := range parts {
		roots = append(roots, filepath.Join(p.Mountpoint, "$Recycle.Bin"))
	}
	var out []binEntry
	for _, root := range roots {
		sids, err := os.ReadDir(root)
		if err != nil {
			continue
		}
		for _, sid := range sids {
			dir := filepath.Join(root, sid.Name())
			files, err := filepath.Glob(filepath.Join(dir, "$I*"))
			if err != nil {
				continue
			}
			for _, f := range files {
				b, err := os.ReadFile(f)
				if err != nil {
					continue
				}
				orig, size, del, ok := parseIFile(b)
				if !ok {
					continue
				}
				stored := filepath.Join(dir, "$R"+strings.TrimPrefix(filepath.Base(f), "$I"))
				if !sys.Exists(stored) {
					continue
				}
				out = append(out, binEntry{Original: orig, Deleted: del, Size: size, Stored: stored, Index: f})
			}
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Deleted.After(out[j].Deleted) })
	return out
}

const restorePointsScript = `
$o = [ordered]@{}
$o.Points = @(Get-ComputerRestorePoint | ForEach-Object { [pscustomobject]@{ When=([Management.ManagementDateTimeConverter]::ToDateTime($_.CreationTime)).ToString('o'); Desc=[string]$_.Description } })
$o.Admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$o
`

func runRecover(c *core.Ctx) (*core.Result, error) {
	r := &core.Result{Title: "Recover deleted things"}
	find := strings.ToLower(c.Str("find", strings.Join(c.Pos, " ")))
	items := readRecycleBin(c)
	t := core.Table{Title: "Recycle Bin (newest first)", Headers: []string{"Deleted", "Size", "Original location"}}
	shown := 0
	for _, it := range items {
		if find != "" && !strings.Contains(strings.ToLower(it.Original), find) {
			continue
		}
		shown++
		if shown <= 40 {
			t.Rows = append(t.Rows, []string{it.Deleted.Format("2006-01-02 15:04"), sys.HumanBytes(it.Size), it.Original})
		}
		if (find == "" && shown <= 25) || (find != "" && shown <= 200) {
			script := fmt.Sprintf("$src = %s; $dst = %s; $idx = %s\nif(Test-Path -LiteralPath $dst){ throw \"Something already exists at $dst - move it first\" }\nNew-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null\nMove-Item -LiteralPath $src -Destination $dst\nRemove-Item -LiteralPath $idx -Force\n\"Restored $dst\"",
				sys.PSQuote(it.Stored), sys.PSQuote(it.Original), sys.PSQuote(it.Index))
			r.Add(core.Finding{ID: "bin-" + shortHash(it.Stored), Severity: core.Info, Title: filepath.Base(it.Original),
				Detail:   "Deleted " + it.Deleted.Format("2006-01-02 15:04") + " from " + filepath.Dir(it.Original),
				Evidence: []string{it.Original}, Data: map[string]any{"bytes": it.Size, "deleted_at": it.Deleted},
				Fixes: []core.Fix{{ID: "restore", Title: "Put it back where it was", Risk: core.Safe,
					Action: core.Action{Kind: core.ActPS, Script: script}}}})
		}
	}
	if len(t.Rows) > 0 {
		r.Tables = append(r.Tables, t)
	}
	var rp struct {
		Points sys.List[struct{ When, Desc string }]
		Admin  bool
	}
	if err := sys.PSJSON(c, restorePointsScript, &rp); err == nil {
		tp := core.Table{Title: "System restore points", Headers: []string{"Created", "Description"}}
		for _, p := range rp.Points {
			when := p.When
			if tm, err := time.Parse(time.RFC3339, p.When); err == nil {
				when = tm.Format("2006-01-02 15:04")
			}
			tp.Rows = append(tp.Rows, []string{when, p.Desc})
		}
		if len(tp.Rows) > 0 {
			r.Tables = append(r.Tables, tp)
		}
		create := core.Fix{ID: "create", Title: "Create a restore point now", Risk: core.Safe, Admin: true,
			Action: core.Action{Kind: core.ActPS, Script: "Enable-ComputerRestore -Drive \"$env:SystemDrive\\\"\nCheckpoint-Computer -Description 'WinSight safety point' -RestorePointType MODIFY_SETTINGS\n'Restore point created'"}}
		if rp.Admin && len(rp.Points) == 0 {
			r.Add(core.Finding{ID: "no-restore-points", Severity: core.Medium, Title: "No system restore points exist",
				Detail: "If an update or driver breaks Windows, there is nothing to roll back to. Turning on System Protection costs a few GB.", Fixes: []core.Fix{create}})
		} else {
			r.Add(core.Finding{ID: "safety-point", Severity: core.Info, Title: "Make a safety restore point before bigger changes",
				Detail: "Recommended before applying registry, service or driver fixes.", Fixes: []core.Fix{create}})
		}
	}
	k := sys.Known()
	if sys.Exists(filepath.Join(k.Local, `Microsoft\Windows\FileHistory\Configuration`)) {
		r.Notes = append(r.Notes, "File History is configured: older versions of your files may be restorable (right-click a folder › Restore previous versions).")
	}
	if sys.Exists(filepath.Join(k.User, "OneDrive")) {
		r.Notes = append(r.Notes, "OneDrive keeps deleted cloud files for 30 days in its own recycle bin: https://onedrive.live.com/?v=recyclebin")
	}
	r.Notes = append(r.Notes, "Emptied the Recycle Bin already? Stop writing to that drive and use Microsoft's free 'Windows File Recovery' (winfr) from the Store as soon as possible.")
	r.Data = items
	r.Summary = fmt.Sprintf("%d items in the Recycle Bin", len(items))
	if find != "" {
		r.Summary += fmt.Sprintf(", %d match %q", shown, find)
	}
	r.Summary += "."
	return r, nil
}
