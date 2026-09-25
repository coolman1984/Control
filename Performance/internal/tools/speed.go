package tools

import (
	"fmt"
	"os"
	"sort"
	"strings"
	"time"

	"github.com/shirou/gopsutil/v4/cpu"
	"github.com/shirou/gopsutil/v4/mem"
	"github.com/shirou/gopsutil/v4/process"

	"github.com/coolman1984/performance/internal/core"
	"github.com/coolman1984/performance/internal/kb"
	"github.com/coolman1984/performance/internal/sys"
)

func init() {
	core.Register(&core.Tool{Name: "startup", Category: "speed", InBrief: true,
		Short:    "Programs that launch at sign-in (registry Run keys + Startup folders)",
		Keywords: []string{"startup", "boot", "login", "slow", "launch", "بدء", "التشغيل", "الاقلاع", "بطيء", "تقيل"},
		Run:      runStartup})
	core.Register(&core.Tool{Name: "services", Category: "speed", InBrief: true,
		Short:    "Background services worth turning off, and third-party updaters",
		Keywords: []string{"services", "service", "background", "updater", "خدمات", "خلفية"},
		Run:      runServices})
	core.Register(&core.Tool{Name: "procs", Category: "speed", InBrief: true,
		Short:    "What is using CPU and memory right now (grouped by app)",
		Aliases:  []string{"top", "ps", "processes"},
		Keywords: []string{"cpu", "ram", "process", "processes", "slow", "lag", "hang", "برامج", "معالج", "رامات", "هنج", "بيهنج", "تهنيج"},
		Run:      runProcs})
	core.Register(&core.Tool{Name: "memory", Category: "speed", InBrief: true,
		Short:    "RAM, page file, memory compression, RAM speed (XMP) and dual-channel check",
		Aliases:  []string{"ram"},
		Keywords: []string{"memory", "ram", "pagefile", "xmp", "رام", "ذاكرة", "الرامات"},
		Run:      runMemory})
	core.Register(&core.Tool{Name: "power", Category: "speed", InBrief: true,
		Short:    "Power plan, hidden Ultimate Performance plan, battery wear",
		Keywords: []string{"power", "battery", "plan", "laptop", "بطارية", "طاقة", "لابتوب"},
		Run:      runPower})
	core.Register(&core.Tool{Name: "tasks", Category: "speed", InBrief: true,
		Short:    "Third-party scheduled tasks (hidden auto-starters and broken tasks)",
		Keywords: []string{"scheduled", "tasks", "task", "scheduler", "مهام", "مجدولة"},
		Run:      runTasks})
	core.Register(&core.Tool{Name: "bloat", Category: "speed", InBrief: true,
		Short:    "Preinstalled / promoted Store apps you can remove",
		Aliases:  []string{"bloatware"},
		Keywords: []string{"bloat", "bloatware", "preinstalled", "apps", "candy", "برامج", "زيادة", "مالهاش لازمة"},
		Run:      runBloat})
}

type startupItem struct {
	Name, Command, Location, ApprovedKey string
	Enabled                              bool
	Folder                               bool
}

const startupScript = `
function Approved($key, $name) {
  $v = (Get-ItemProperty -Path $key -Name $name -ErrorAction SilentlyContinue).$name
  if($v -is [byte[]] -and $v.Length -gt 0){ return (($v[0] % 2) -eq 0) }
  return $true
}
$items = New-Object System.Collections.ArrayList
$sa = 'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved'
$runs = @(
  @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Run', "HKCU:\$sa\Run"),
  @('HKLM:\Software\Microsoft\Windows\CurrentVersion\Run', "HKLM:\$sa\Run"),
  @('HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run', "HKLM:\$sa\Run32")
)
foreach($r in $runs){
  $k = Get-Item -Path $r[0]
  if(-not $k){ continue }
  foreach($n in $k.GetValueNames()){
    if($n -eq ''){ continue }
    [void]$items.Add([pscustomobject]@{ Name=$n; Command=[string]$k.GetValue($n); Location=$r[0]; ApprovedKey=$r[1]; Enabled=(Approved $r[1] $n); Folder=$false })
  }
}
$folders = @(
  @([Environment]::GetFolderPath('Startup'), "HKCU:\$sa\StartupFolder"),
  @([Environment]::GetFolderPath('CommonStartup'), "HKLM:\$sa\StartupFolder")
)
$sh = New-Object -ComObject WScript.Shell
foreach($f in $folders){
  if(-not $f[0] -or -not (Test-Path $f[0])){ continue }
  foreach($file in Get-ChildItem -LiteralPath $f[0] -File -Force){
    if($file.Name -eq 'desktop.ini'){ continue }
    $cmd = $file.FullName
    if($file.Extension -eq '.lnk'){ $t = $sh.CreateShortcut($file.FullName); if($t.TargetPath){ $cmd = '"' + $t.TargetPath + '" ' + $t.Arguments } }
    [void]$items.Add([pscustomobject]@{ Name=$file.Name; Command=$cmd; Location=$f[0]; ApprovedKey=$f[1]; Enabled=(Approved $f[1] $file.Name); Folder=$true })
  }
}
@{ items = @($items) }
`

// heavyStartup are apps that are known to slow sign-in noticeably.
var heavyStartup = []string{"teams", "spotify", "discord", "steam", "epicgames", "skype", "creative cloud", "adobe", "zoom",
	"onedrive", "dropbox", "googledrive", "icloud", "utorrent", "bittorrent", "qbittorrent", "msedge", "edgeautolaunch", "opera",
	"battle.net", "origin", "eadesktop", "ubisoft", "razer", "logitech", "lghub", "corsair", "icue", "armoury", "wallpaper",
	"ccleaner", "avast", "mcafee", "norton", "whatsapp", "telegram", "slack", "notion", "figma", "cortana"}

func isHeavy(name, cmd string) bool {
	s := strings.ToLower(name + " " + cmd)
	for _, h := range heavyStartup {
		if strings.Contains(s, h) {
			return true
		}
	}
	return false
}

func runStartup(c *core.Ctx) (*core.Result, error) {
	r := &core.Result{Title: "Startup programs"}
	var out struct{ Items sys.List[startupItem] }
	if err := sys.PSJSON(c, startupScript, &out); windowsOnly(r, err) || err != nil {
		return r, nil
	}
	t := core.Table{Headers: []string{"On", "Name", "Where", "Command"}}
	enabled := 0
	for _, it := range out.Items {
		state := "no"
		if it.Enabled {
			state = "yes"
			enabled++
		}
		where := "Registry"
		if it.Folder {
			where = "Startup folder"
		}
		if strings.HasPrefix(it.Location, "HKLM") {
			where += " (all users)"
		}
		t.Rows = append(t.Rows, []string{state, it.Name, where, sys.Truncate(it.Command, 80)})
		id := slug(it.Name)
		admin := strings.HasPrefix(it.ApprovedKey, "HKLM")
		if exe, missing := targetMissing(it.Command); missing {
			remove := fmt.Sprintf("Remove-ItemProperty -Path %s -Name %s -Force", sys.PSQuote(it.Location), sys.PSQuote(it.Name))
			undo := regSetScript(it.Location, it.Name, "String", it.Command)
			if it.Folder {
				remove = ""
			}
			f := core.Finding{ID: "broken-" + id, Severity: core.Medium, Title: "Startup entry points to a missing program: " + it.Name,
				Detail:   "The program was uninstalled or moved but its auto-start entry stayed behind. Windows tries (and fails) to launch it at every sign-in.",
				Evidence: []string{it.Command, "missing: " + exe}}
			if remove != "" {
				f.Fixes = []core.Fix{{ID: "remove", Title: "Remove the dead entry", Risk: core.Safe, Admin: admin, Reversible: true,
					Action: core.Action{Kind: core.ActPS, Script: remove}, Undo: &core.Action{Kind: core.ActPS, Script: undo}}}
			} else {
				f.Fixes = []core.Fix{{ID: "remove", Title: "Quarantine the dead shortcut", Risk: core.Safe, Reversible: true,
					Action: core.Action{Kind: core.ActQuarantine, Paths: []string{it.Location + string(os.PathSeparator) + it.Name}}}}
			}
			r.Add(f)
			continue
		}
		if !it.Enabled {
			continue
		}
		sev := core.Info
		if isHeavy(it.Name, it.Command) {
			sev = core.Low
		}
		disable := regSetScript(it.ApprovedKey, it.Name, "Binary", "([byte[]](3,0,0,0,0,0,0,0,0,0,0,0))")
		enable := regSetScript(it.ApprovedKey, it.Name, "Binary", "([byte[]](2,0,0,0,0,0,0,0,0,0,0,0))")
		r.Add(core.Finding{ID: "item-" + id, Severity: sev, Title: "Starts with Windows: " + it.Name,
			Detail:   "Disabling only stops the auto-start (exactly like Task Manager › Startup); the app still works when you open it.",
			Evidence: []string{it.Command},
			Fixes: []core.Fix{{ID: "disable", Title: "Disable auto-start", Risk: core.Safe, Admin: admin, Reversible: true,
				Action: core.Action{Kind: core.ActPS, Script: disable}, Undo: &core.Action{Kind: core.ActPS, Script: enable}}}})
	}
	r.Tables = append(r.Tables, t)
	r.Data = out.Items
	r.Summary = fmt.Sprintf("%d startup entries, %d enabled.", len(out.Items), enabled)
	if enabled > 10 {
		r.Add(core.Finding{ID: "too-many", Severity: core.Medium, Title: fmt.Sprintf("%d programs start with Windows", enabled),
			Detail: "Each one costs sign-in time and background RAM. Keep security software, drivers' control panels you use, and cloud sync; disable the rest."})
	}
	r.Notes = append(r.Notes, "Scheduled tasks can also start programs silently — see `tasks`. Boot slowdowns are measured in `boot`.")
	return r, nil
}

type svc struct {
	Name, DisplayName, StartMode, State, PathName string
}

func startModeNum(m string) int {
	switch strings.ToLower(m) {
	case "boot":
		return 0
	case "system":
		return 1
	case "auto":
		return 2
	case "manual":
		return 3
	case "disabled":
		return 4
	}
	return 3
}

func isMicrosoftService(path string) bool {
	p := strings.ToLower(path)
	return strings.Contains(p, `\windows\system32\`) || strings.Contains(p, `\windows\syswow64\`) ||
		strings.Contains(p, `windows defender`) || strings.Contains(p, `\windows\microsoft.net\`) ||
		strings.Contains(p, `\microsoft\windows defender`) || strings.Contains(p, `\windows\servicing\`) || p == ""
}

func runServices(c *core.Ctx) (*core.Result, error) {
	r := &core.Result{Title: "Services"}
	var out struct{ Items sys.List[svc] }
	err := sys.PSJSON(c, `@{ items = @(Get-CimInstance Win32_Service | Select-Object Name,DisplayName,StartMode,State,PathName) }`, &out)
	if windowsOnly(r, err) || err != nil {
		return r, nil
	}
	byName := map[string]svc{}
	for _, s := range out.Items {
		byName[strings.ToLower(s.Name)] = s
	}
	for _, a := range kb.ServiceAdvices {
		s, ok := byName[strings.ToLower(a.Name)]
		if !ok || startModeNum(s.StartMode) >= a.Want {
			continue
		}
		want := map[int]string{3: "Manual", 4: "Disabled"}[a.Want]
		r.Add(core.Finding{ID: slug(a.Name), Severity: a.Sev, Title: fmt.Sprintf("%s is %s (%s)", a.Title, strings.ToLower(s.StartMode), strings.ToLower(s.State)),
			Detail: a.Why,
			Fixes: []core.Fix{{ID: "set", Title: "Set to " + want, Risk: a.Risk, Admin: true, Reversible: true,
				Action: core.Action{Kind: core.ActPS, Script: serviceStartScript(a.Name, a.Want, map[bool]string{true: "stop"}[a.Want == 4])},
				Undo:   &core.Action{Kind: core.ActPS, Script: serviceStartScript(a.Name, startModeNum(s.StartMode), "")}}}})
	}
	t := core.Table{Title: "Third-party services that start automatically", Headers: []string{"Service", "State", "Program"}}
	for _, s := range out.Items {
		if isMicrosoftService(s.PathName) || !strings.EqualFold(s.StartMode, "auto") {
			continue
		}
		t.Rows = append(t.Rows, []string{s.DisplayName, s.State, sys.Truncate(exePath(s.PathName), 70)})
		if label, ok := kb.KnownUpdaters[s.Name]; ok {
			r.Add(core.Finding{ID: "updater-" + slug(s.Name), Severity: core.Low, Title: label + " runs all the time (" + s.Name + ")",
				Detail: "An always-on updater service. On Manual the app still updates when it runs (most also use a scheduled task).",
				Fixes: []core.Fix{{ID: "manual", Title: "Set to Manual", Risk: core.Safe, Admin: true, Reversible: true,
					Action: core.Action{Kind: core.ActPS, Script: serviceStartScript(s.Name, 3, "")},
					Undo:   &core.Action{Kind: core.ActPS, Script: serviceStartScript(s.Name, 2, "start")}}}})
			continue
		}
		if _, missing := targetMissing(s.PathName); missing {
			r.Add(core.Finding{ID: "broken-" + slug(s.Name), Severity: core.Medium, Title: "Service points to a missing program: " + s.DisplayName,
				Detail: "Leftover from uninstalled software; Windows logs an error for it at every boot (event 7000).", Evidence: []string{s.PathName},
				Fixes: []core.Fix{{ID: "disable", Title: "Disable the orphaned service", Risk: core.Safe, Admin: true, Reversible: true,
					Action: core.Action{Kind: core.ActPS, Script: serviceStartScript(s.Name, 4, "")},
					Undo:   &core.Action{Kind: core.ActPS, Script: serviceStartScript(s.Name, 2, "")}},
					{ID: "delete", Title: "Delete the orphaned service", Risk: core.Moderate, Admin: true,
						Action: core.Action{Kind: core.ActExec, Cmd: []string{"sc.exe", "delete", s.Name}}}}})
		}
	}
	sort.Slice(t.Rows, func(i, j int) bool { return t.Rows[i][0] < t.Rows[j][0] })
	if len(t.Rows) > 0 {
		r.Tables = append(r.Tables, t)
	}
	r.Summary = fmt.Sprintf("%d services, %d third-party ones start automatically.", len(out.Items), len(t.Rows))
	return r, nil
}

type procGroup struct {
	Name  string  `json:"name"`
	Count int     `json:"count"`
	RSS   uint64  `json:"rss_bytes"`
	CPU   float64 `json:"cpu_percent"`
}

// sampleProcs groups processes by name with CPU measured over window.
func sampleProcs(c *core.Ctx, window time.Duration) ([]procGroup, error) {
	ps, err := process.ProcessesWithContext(c)
	if err != nil {
		return nil, err
	}
	type snap struct {
		p    *process.Process
		name string
		cpu  float64
	}
	snaps := make([]snap, 0, len(ps))
	for _, p := range ps {
		name, err := p.NameWithContext(c)
		if err != nil || name == "" {
			continue
		}
		t, err := p.TimesWithContext(c)
		if err != nil {
			snaps = append(snaps, snap{p, name, -1})
			continue
		}
		snaps = append(snaps, snap{p, name, t.User + t.System})
	}
	select {
	case <-time.After(window):
	case <-c.Done():
		return nil, c.Err()
	}
	ncpu, _ := cpu.Counts(true)
	if ncpu == 0 {
		ncpu = 1
	}
	groups := map[string]*procGroup{}
	for _, s := range snaps {
		key := strings.ToLower(strings.TrimSuffix(s.name, ".exe"))
		g := groups[key]
		if g == nil {
			g = &procGroup{Name: strings.TrimSuffix(s.name, ".exe")}
			groups[key] = g
		}
		g.Count++
		if mi, err := s.p.MemoryInfoWithContext(c); err == nil {
			g.RSS += mi.RSS
		}
		if s.cpu >= 0 {
			if t, err := s.p.TimesWithContext(c); err == nil {
				g.CPU += (t.User + t.System - s.cpu) / window.Seconds() * 100 / float64(ncpu)
			}
		}
	}
	out := make([]procGroup, 0, len(groups))
	for _, g := range groups {
		if g.Name == "System Idle Process" || g.Name == "Idle" {
			continue
		}
		out = append(out, *g)
	}
	return out, nil
}

func runProcs(c *core.Ctx) (*core.Result, error) {
	r := &core.Result{Title: "What's using the PC"}
	c.Progress("sampling CPU for 1.5 s")
	groups, err := sampleProcs(c, 1500*time.Millisecond)
	if err != nil {
		return r, err
	}
	vm, _ := mem.VirtualMemoryWithContext(c)
	sort.Slice(groups, func(i, j int) bool { return groups[i].RSS > groups[j].RSS })
	tm := core.Table{Title: "By memory", Headers: []string{"App", "Procs", "RAM", "CPU"}}
	for i, g := range groups {
		if i >= 12 {
			break
		}
		tm.Rows = append(tm.Rows, []string{g.Name, fmt.Sprint(g.Count), sys.HumanBytes(int64(g.RSS)), fmt.Sprintf("%.1f%%", g.CPU)})
		if vm != nil && g.RSS > vm.Total/4 {
			r.Add(core.Finding{ID: "ram-" + slug(g.Name), Severity: core.Medium, Title: fmt.Sprintf("%s uses %s (%s of RAM)", g.Name, sys.HumanBytes(int64(g.RSS)), sys.Pct(float64(g.RSS), float64(vm.Total))),
				Detail: "Close tabs/windows you don't need or restart the app; browsers with many tabs are the usual culprit. For browsers enable Memory Saver / Sleeping Tabs."})
		}
	}
	sort.Slice(groups, func(i, j int) bool { return groups[i].CPU > groups[j].CPU })
	tc := core.Table{Title: "By CPU (last 1.5 s)", Headers: []string{"App", "Procs", "CPU", "RAM"}}
	for i, g := range groups {
		if i >= 8 || g.CPU < 0.5 {
			break
		}
		tc.Rows = append(tc.Rows, []string{g.Name, fmt.Sprint(g.Count), fmt.Sprintf("%.1f%%", g.CPU), sys.HumanBytes(int64(g.RSS))})
		if g.CPU > 40 {
			r.Add(core.Finding{ID: "cpu-" + slug(g.Name), Severity: core.Medium, Title: fmt.Sprintf("%s is using %.0f%% CPU", g.Name, g.CPU),
				Detail: cpuHint(g.Name)})
		}
	}
	r.Tables = append(r.Tables, tm)
	if len(tc.Rows) > 0 {
		r.Tables = append(r.Tables, tc)
	}
	r.Data = groups
	if vm != nil {
		r.Summary = fmt.Sprintf("%d apps running; RAM %s of %s used (%.0f%%).", len(groups), sys.HumanBytes(int64(vm.Used)), sys.HumanBytes(int64(vm.Total)), vm.UsedPercent)
	}
	return r, nil
}

func cpuHint(name string) string {
	switch strings.ToLower(name) {
	case "msmpeng", "mssense":
		return "Microsoft Defender is scanning. If constant, add exclusions for big dev/game folders or schedule scans."
	case "searchindexer", "searchprotocolhost":
		return "Windows Search is indexing. It calms down after it finishes; exclude huge folders in Indexing Options."
	case "tiworker", "trustedinstaller", "wuauclt", "usoclient":
		return "Windows Update is installing. Let it finish, then restart."
	case "compattelrunner":
		return "Microsoft compatibility telemetry. Harmless but heavy; `services` can turn off DiagTrack."
	case "system":
		return "Kernel/driver work. Constant high CPU here usually means a bad driver — check `drivers` and `events`."
	case "dwm":
		return "Desktop Window Manager. High use means GPU driver problems or many animated windows."
	}
	return "Check whether this app is doing real work; otherwise restart or update it."
}

type memPS struct {
	Compression bool
	AutoPF      bool
	PageFiles   sys.List[struct {
		Name                 string
		AllocatedMB, UsageMB int64
		PeakMB               int64
	}]
	Sticks sys.List[struct {
		Capacity     int64
		Speed        int64
		Configured   int64
		Manufacturer string
		PartNumber   string
		Slot         string
	}]
	Slots int
}

const memScript = `
$o = [ordered]@{}
try { $o.Compression = [bool](Get-MMAgent).MemoryCompression } catch { $o.Compression = $false }
$o.AutoPF = [bool](Get-CimInstance Win32_ComputerSystem).AutomaticManagedPagefile
$o.PageFiles = @(Get-CimInstance Win32_PageFileUsage | ForEach-Object { [pscustomobject]@{ Name=$_.Name; AllocatedMB=[int64]$_.AllocatedBaseSize; UsageMB=[int64]$_.CurrentUsage; PeakMB=[int64]$_.PeakUsage } })
$o.Sticks = @(Get-CimInstance Win32_PhysicalMemory | ForEach-Object { [pscustomobject]@{ Capacity=[int64]$_.Capacity; Speed=[int64]$_.Speed; Configured=[int64]$_.ConfiguredClockSpeed; Manufacturer=[string]$_.Manufacturer; PartNumber=([string]$_.PartNumber).Trim(); Slot=[string]$_.DeviceLocator } })
$o.Slots = [int](Get-CimInstance Win32_PhysicalMemoryArray | Measure-Object -Property MemoryDevices -Sum).Sum
$o
`

func runMemory(c *core.Ctx) (*core.Result, error) {
	r := &core.Result{Title: "Memory"}
	vm, err := mem.VirtualMemoryWithContext(c)
	if err != nil {
		return r, err
	}
	sw, _ := mem.SwapMemoryWithContext(c)
	t := core.Table{Headers: []string{"Metric", "Value"}}
	t.Rows = append(t.Rows, []string{"Installed RAM", sys.HumanBytes(int64(vm.Total))},
		[]string{"In use", fmt.Sprintf("%s (%.0f%%)", sys.HumanBytes(int64(vm.Used)), vm.UsedPercent)},
		[]string{"Available", sys.HumanBytes(int64(vm.Available))})
	if sw != nil && sw.Total > 0 {
		t.Rows = append(t.Rows, []string{"Page file in use", fmt.Sprintf("%s of %s", sys.HumanBytes(int64(sw.Used)), sys.HumanBytes(int64(sw.Total)))})
	}
	switch {
	case vm.UsedPercent > 90:
		r.Add(core.Finding{ID: "ram-full", Severity: core.High, Title: fmt.Sprintf("RAM is %.0f%% full", vm.UsedPercent),
			Detail: "Windows is swapping to disk, which makes everything stutter. Run `procs` to find the hog; consider more RAM."})
	case vm.UsedPercent > 80:
		r.Add(core.Finding{ID: "ram-high", Severity: core.Medium, Title: fmt.Sprintf("RAM is %.0f%% used", vm.UsedPercent),
			Detail: "Close heavy apps (`procs`) or trim startup programs (`startup`)."})
	}
	if vm.Total < 8<<30 {
		r.Add(core.Finding{ID: "ram-small", Severity: core.Medium, Title: "Only " + sys.HumanBytes(int64(vm.Total)) + " of RAM",
			Detail: "Windows 11 plus a modern browser needs 8 GB to feel smooth; 16 GB for multitasking."})
	}
	var m memPS
	if err := sys.PSJSON(c, memScript, &m); err == nil {
		t.Rows = append(t.Rows, []string{"Memory compression", onOff(m.Compression)}, []string{"Page file managed by Windows", onOff(m.AutoPF)})
		if len(m.PageFiles) == 0 {
			r.Add(core.Finding{ID: "no-pagefile", Severity: core.High, Title: "No page file",
				Detail: "Without a page file, apps crash with 'out of memory' even when RAM looks free, and crash dumps can't be written.",
				Fixes: []core.Fix{{ID: "auto", Title: "Let Windows manage the page file", Risk: core.Safe, Admin: true,
					Action: core.Action{Kind: core.ActPS, Script: "$cs = Get-CimInstance Win32_ComputerSystem\nSet-CimInstance -InputObject $cs -Property @{AutomaticManagedPagefile=$true}"}}}})
		}
		for _, pf := range m.PageFiles {
			t.Rows = append(t.Rows, []string{"Page file " + pf.Name, fmt.Sprintf("%d MB allocated, %d MB used, peak %d MB", pf.AllocatedMB, pf.UsageMB, pf.PeakMB)})
			if pf.AllocatedMB > 0 && pf.PeakMB*10 > pf.AllocatedMB*9 && !m.AutoPF {
				r.Add(core.Finding{ID: "pagefile-small", Severity: core.Medium, Title: "Page file almost filled up at its peak",
					Detail: "A manually sized page file that fills up causes 'low memory' crashes.",
					Fixes: []core.Fix{{ID: "auto", Title: "Let Windows manage the page file", Risk: core.Safe, Admin: true,
						Action: core.Action{Kind: core.ActPS, Script: "$cs = Get-CimInstance Win32_ComputerSystem\nSet-CimInstance -InputObject $cs -Property @{AutomaticManagedPagefile=$true}"}}}})
			}
		}
		if !m.Compression && vm.Total <= 16<<30 {
			r.Add(core.Finding{ID: "compression-off", Severity: core.Low, Title: "Memory compression is off",
				Detail: "Compression lets Windows keep more in RAM instead of swapping to disk — a free win on ≤16 GB machines.",
				Fixes: []core.Fix{{ID: "enable", Title: "Enable memory compression", Risk: core.Safe, Admin: true, Reversible: true,
					Action: core.Action{Kind: core.ActPS, Script: "Enable-MMAgent -MemoryCompression"},
					Undo:   &core.Action{Kind: core.ActPS, Script: "Disable-MMAgent -MemoryCompression"}}}})
		}
		for _, s := range m.Sticks {
			t.Rows = append(t.Rows, []string{"Stick " + s.Slot, fmt.Sprintf("%s %s %s — rated %d MT/s, running %d MT/s", sys.HumanBytes(s.Capacity), s.Manufacturer, s.PartNumber, s.Speed, s.Configured)})
		}
		if len(m.Sticks) > 0 {
			s := m.Sticks[0]
			if s.Speed > 0 && s.Configured > 0 && s.Configured*100 < s.Speed*85 {
				r.Add(core.Finding{ID: "ram-slow", Severity: core.Low, Title: fmt.Sprintf("RAM runs at %d MT/s but is rated %d", s.Configured, s.Speed),
					Detail: "Your memory runs below its rated speed. Enabling XMP/EXPO/DOCP in the BIOS can give 5–15% more performance in games and heavy apps.",
					Fixes:  []core.Fix{{ID: "bios", Title: "Enable XMP/EXPO in BIOS", Risk: core.Moderate, Action: core.Action{Kind: core.ActManual, Manual: "Restart into BIOS (Del/F2), find XMP/EXPO/DOCP profile, enable it, save. If the PC becomes unstable, turn it back off."}}}})
			}
			if len(m.Sticks) == 1 && m.Slots >= 2 {
				r.Add(core.Finding{ID: "single-channel", Severity: core.Low, Title: "Single RAM stick (single-channel)",
					Detail: fmt.Sprintf("You have 1 stick and %d slots. Adding an identical stick enables dual-channel: much faster integrated graphics and up to ~20%% in memory-heavy tasks.", m.Slots)})
			}
		}
	}
	r.Tables = append(r.Tables, t)
	r.Summary = fmt.Sprintf("%s RAM, %.0f%% in use.", sys.HumanBytes(int64(vm.Total)), vm.UsedPercent)
	return r, nil
}

func onOff(b bool) string {
	if b {
		return "on"
	}
	return "off"
}

type powerPS struct {
	Active     string
	ActiveName string
	Plans      sys.List[struct{ Guid, Name string }]
	Battery    bool
	OnAC       bool
	Design     int64
	Full       int64
	Cycles     int64
}

const powerScript = `
$o = [ordered]@{ Plans=@() }
$lines = powercfg /list
foreach($l in $lines){ if($l -match '([0-9a-fA-F-]{36})\s+\((.+?)\)(\s*\*)?'){ $o.Plans += [pscustomobject]@{Guid=$matches[1]; Name=$matches[2]}; if($matches[3]){ $o.Active=$matches[1]; $o.ActiveName=$matches[2] } } }
$b = Get-CimInstance Win32_Battery
$o.Battery = [bool]$b
Add-Type -AssemblyName System.Windows.Forms
$o.OnAC = ([System.Windows.Forms.SystemInformation]::PowerStatus.PowerLineStatus -eq 'Online')
if($b){
  $x = "$env:TEMP\winsight-battery.xml"
  powercfg /batteryreport /xml /output $x /duration 1 | Out-Null
  if(Test-Path $x){
    [xml]$doc = Get-Content $x
    $bat = @($doc.BatteryReport.Batteries.Battery)[0]
    $o.Design = [int64]$bat.DesignCapacity; $o.Full = [int64]$bat.FullChargeCapacity; $o.Cycles = [int64]$bat.CycleCount
    Remove-Item $x -Force
  }
}
$o
`

const (
	planBalanced = "381b4222-f694-41f0-9685-ff5bb260df2e"
	planHigh     = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
	planSaver    = "a1841308-3542-4d7e-8b00-5ae2e8bb67e9"
	planUltimate = "e9a42b02-d5df-448d-aa00-03f14749eb61"
)

func runPower(c *core.Ctx) (*core.Result, error) {
	r := &core.Result{Title: "Power"}
	var p powerPS
	if err := sys.PSJSON(c, powerScript, &p); windowsOnly(r, err) || err != nil {
		return r, nil
	}
	t := core.Table{Headers: []string{"Plan", "GUID", "Active"}}
	for _, pl := range p.Plans {
		t.Rows = append(t.Rows, []string{pl.Name, pl.Guid, map[bool]string{true: "●"}[strings.EqualFold(pl.Guid, p.Active)]})
	}
	r.Tables = append(r.Tables, t)
	back := &core.Action{Kind: core.ActExec, Cmd: []string{"powercfg", "/setactive", p.Active}}
	switch strings.ToLower(p.Active) {
	case planSaver:
		sev := core.Medium
		if p.Battery && !p.OnAC {
			sev = core.Info
		}
		r.Add(core.Finding{ID: "power-saver", Severity: sev, Title: "Power saver plan is active",
			Detail: "Power saver caps CPU speed. Fine on battery; on AC power it makes everything slower.",
			Fixes: []core.Fix{{ID: "balanced", Title: "Switch to Balanced", Risk: core.Safe, Reversible: true,
				Action: core.Action{Kind: core.ActExec, Cmd: []string{"powercfg", "/setactive", planBalanced}}, Undo: back}}})
	case planBalanced:
		if !p.Battery {
			f := core.Finding{ID: "desktop-balanced", Severity: core.Info, Title: "Desktop PC on the Balanced plan",
				Detail: "On a desktop, High performance keeps the CPU from parking cores and reacts faster. Windows also hides an 'Ultimate Performance' plan.",
				Fixes: []core.Fix{{ID: "high", Title: "Switch to High performance", Risk: core.Safe, Reversible: true,
					Action: core.Action{Kind: core.ActExec, Cmd: []string{"powercfg", "/setactive", planHigh}}, Undo: back},
					{ID: "ultimate", Title: "Unlock and use the hidden Ultimate Performance plan", Risk: core.Safe, Reversible: true,
						Action: core.Action{Kind: core.ActPS, Script: "$o = powercfg -duplicatescheme " + planUltimate + "\nif($o -match '([0-9a-fA-F-]{36})'){ powercfg /setactive $matches[1] }"}, Undo: back}}}
			r.Add(f)
		}
	}
	if p.Battery && p.Design > 0 && p.Full > 0 {
		health := float64(p.Full) * 100 / float64(p.Design)
		t2 := core.Table{Title: "Battery", Headers: []string{"Design capacity", "Full charge now", "Health", "Cycles"}}
		t2.Rows = append(t2.Rows, []string{fmt.Sprintf("%d mWh", p.Design), fmt.Sprintf("%d mWh", p.Full), fmt.Sprintf("%.0f%%", health), fmt.Sprint(p.Cycles)})
		r.Tables = append(r.Tables, t2)
		if health < 80 {
			sev := core.Low
			if health < 60 {
				sev = core.Medium
			}
			r.Add(core.Finding{ID: "battery-wear", Severity: sev, Title: fmt.Sprintf("Battery holds %.0f%% of its original charge", health),
				Detail: "Batteries wear with cycles and heat. Below ~60% runtime drops sharply; plan a replacement. Keeping charge between 20–80% slows wear."})
		}
	}
	r.Summary = "Active plan: " + p.ActiveName
	return r, nil
}

type taskPS struct {
	Name, Path, State, Author, Exec, Args, Triggers string
}

const tasksScript = `
@{ items = @(Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\Microsoft\*' } | ForEach-Object {
  $a = @($_.Actions)[0]
  [pscustomobject]@{ Name=$_.TaskName; Path=$_.TaskPath; State=[string]$_.State; Author=[string]$_.Author;
    Exec=[string]$a.Execute; Args=[string]$a.Arguments;
    Triggers=(@($_.Triggers | ForEach-Object { $_.CimClass.CimClassName -replace 'MSFT_Task','' -replace 'Trigger','' }) -join ',') }
}) }
`

func runTasks(c *core.Ctx) (*core.Result, error) {
	r := &core.Result{Title: "Scheduled tasks (non-Microsoft)"}
	var out struct{ Items sys.List[taskPS] }
	if err := sys.PSJSON(c, tasksScript, &out); windowsOnly(r, err) || err != nil {
		return r, nil
	}
	t := core.Table{Headers: []string{"Task", "State", "When", "Runs"}}
	active := 0
	for _, tk := range out.Items {
		full := tk.Path + tk.Name
		t.Rows = append(t.Rows, []string{full, tk.State, tk.Triggers, sys.Truncate(tk.Exec+" "+tk.Args, 60)})
		if strings.EqualFold(tk.State, "Disabled") {
			continue
		}
		active++
		enable := fmt.Sprintf("Enable-ScheduledTask -TaskPath %s -TaskName %s", sys.PSQuote(tk.Path), sys.PSQuote(tk.Name))
		disable := fmt.Sprintf("Disable-ScheduledTask -TaskPath %s -TaskName %s", sys.PSQuote(tk.Path), sys.PSQuote(tk.Name))
		if exe, missing := targetMissing(tk.Exec); missing && tk.Exec != "" {
			r.Add(core.Finding{ID: "broken-" + slug(tk.Name), Severity: core.Low, Title: "Task runs a missing program: " + full,
				Detail: "Leftover of uninstalled software; fails silently on every trigger.", Evidence: []string{"missing: " + exe},
				Fixes: []core.Fix{{ID: "disable", Title: "Disable the task", Risk: core.Safe, Admin: true, Reversible: true,
					Action: core.Action{Kind: core.ActPS, Script: disable}, Undo: &core.Action{Kind: core.ActPS, Script: enable}}}})
			continue
		}
		lower := strings.ToLower(tk.Triggers)
		if strings.Contains(lower, "logon") || strings.Contains(lower, "boot") {
			r.Add(core.Finding{ID: "autostart-" + slug(tk.Name), Severity: core.Info, Title: "Hidden auto-start via scheduled task: " + full,
				Detail: "Runs at sign-in/boot but does not appear in Task Manager's Startup tab.", Evidence: []string{tk.Exec + " " + tk.Args},
				Fixes: []core.Fix{{ID: "disable", Title: "Disable the task", Risk: core.Moderate, Admin: true, Reversible: true,
					Action: core.Action{Kind: core.ActPS, Script: disable}, Undo: &core.Action{Kind: core.ActPS, Script: enable}}}})
		}
	}
	r.Tables = append(r.Tables, t)
	r.Summary = fmt.Sprintf("%d third-party tasks, %d active.", len(out.Items), active)
	return r, nil
}

func runBloat(c *core.Ctx) (*core.Result, error) {
	r := &core.Result{Title: "Preinstalled apps"}
	var out struct {
		Items sys.List[struct{ Name, PackageFullName string }]
	}
	if err := sys.PSJSON(c, `@{ items = @(Get-AppxPackage | Where-Object { -not $_.IsFramework -and $_.SignatureKind -ne 'System' } | Select-Object Name,PackageFullName) }`, &out); windowsOnly(r, err) || err != nil {
		return r, nil
	}
	t := core.Table{Headers: []string{"App", "Package"}}
	for _, a := range out.Items {
		label, ok := kb.Bloat[a.Name]
		if !ok {
			continue
		}
		t.Rows = append(t.Rows, []string{label, a.Name})
		r.Add(core.Finding{ID: slug(a.Name), Severity: core.Info, Title: label,
			Detail: "Preinstalled or promoted app. Removing it for your account is harmless; reinstall any time from the Microsoft Store.",
			Fixes: []core.Fix{{ID: "remove", Title: "Uninstall " + label, Risk: core.Moderate,
				Action: core.Action{Kind: core.ActPS, Script: "Get-AppxPackage -Name " + sys.PSQuote(a.Name) + " | Remove-AppxPackage"}}}})
	}
	if len(t.Rows) > 0 {
		r.Tables = append(r.Tables, t)
	}
	r.Summary = fmt.Sprintf("%d removable preinstalled apps (of %d Store apps).", len(t.Rows), len(out.Items))
	return r, nil
}
