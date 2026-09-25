package tools

import (
	"context"
	"encoding/binary"
	"fmt"
	"math/rand"
	"net"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/shirou/gopsutil/v4/host"

	"github.com/coolman1984/performance/internal/core"
	"github.com/coolman1984/performance/internal/kb"
	"github.com/coolman1984/performance/internal/sys"
)

func init() {
	core.Register(&core.Tool{Name: "health", Category: "health", InBrief: true,
		Short:    "Disk health (SMART/wear/temperature), TRIM, pending reboot, uptime, antivirus, firewall, updates, activation",
		Aliases:  []string{"status"},
		Keywords: []string{"health", "smart", "ssd", "disk", "antivirus", "firewall", "defender", "صحة", "سليم", "حالة"},
		Run:      runHealth})
	core.Register(&core.Tool{Name: "events", Category: "health", InBrief: true,
		Short:    "Recent errors in the Windows event logs, explained in plain words",
		Params:   []core.Param{{Name: "days", Desc: "look back N days", Default: "7", Type: "int"}},
		Aliases:  []string{"logs"},
		Keywords: []string{"events", "errors", "log", "logs", "event", "اخطاء", "أخطاء", "سجل"},
		Run:      runEvents})
	core.Register(&core.Tool{Name: "crashes", Category: "health", InBrief: true,
		Short:    "Blue screens (decoded stop codes), unexpected shutdowns and crashing apps",
		Aliases:  []string{"bsod"},
		Keywords: []string{"crash", "crashes", "bsod", "blue", "screen", "freeze", "restart", "reboot", "شاشة", "زرقاء", "بيقفل", "بيعيد", "كراش"},
		Run:      runCrashes})
	core.Register(&core.Tool{Name: "drivers", Category: "health", InBrief: true,
		Short:    "Devices with errors, missing drivers, generic GPU driver, ghost devices",
		Keywords: []string{"driver", "drivers", "device", "devices", "تعريف", "تعريفات", "جهاز"},
		Run:      runDrivers})
	core.Register(&core.Tool{Name: "updates", Category: "health", InBrief: true,
		Short:    "Windows Update history, failed updates decoded, paused updates",
		Params:   []core.Param{{Name: "check", Desc: "also search for pending updates (slow)", Type: "bool"}},
		Keywords: []string{"update", "updates", "windows update", "تحديث", "تحديثات", "ابديت"},
		Run:      runUpdates})
	core.Register(&core.Tool{Name: "integrity", Category: "health", InBrief: true, Admin: true,
		Short:    "System file & component store corruption, dirty volumes (sfc/DISM/chkdsk)",
		Aliases:  []string{"sfc"},
		Keywords: []string{"corrupt", "corruption", "sfc", "dism", "chkdsk", "repair", "تلف", "بايظ", "ملفات النظام"},
		Run:      runIntegrity})
	core.Register(&core.Tool{Name: "net", Category: "health", InBrief: true,
		Short:    "Internet latency, DNS speed benchmark, Wi-Fi signal, proxy and hosts-file hijacks",
		Aliases:  []string{"network", "internet"},
		Keywords: []string{"internet", "network", "wifi", "dns", "ping", "slow internet", "proxy", "hosts", "نت", "انترنت", "واي", "شبكة"},
		Run:      runNet})
	core.Register(&core.Tool{Name: "boot", Category: "health", InBrief: true, Admin: true,
		Short:    "How long boot really takes and which apps/drivers/services slow it (Windows' own boot diagnostics)",
		Keywords: []string{"boot", "startup time", "slow boot", "bios", "الاقلاع", "بيفتح", "بطيء", "فتح"},
		Run:      runBoot})
}

type healthPS struct {
	PendingReboot sys.List[string]
	Disks         sys.List[struct {
		Name, Media, Bus, Health, Op string
		Size                         int64
		Wear, Temp                   int
		ReadErr, WriteErr            int64
		PowerOnHours                 int64
	}]
	TrimOff    bool
	DefenderRT *bool
	SigAgeDays int
	AV         sys.List[struct {
		Name  string
		State int64
	}]
	Firewall   sys.List[struct{ Name, Enabled string }]
	LastUpdate string
	LastKB     string
	Licensed   int
}

const healthScript = `
$o = [ordered]@{}
$pend = @()
if(Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'){ $pend += 'Component servicing' }
if(Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'){ $pend += 'Windows Update' }
if((Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -Name PendingFileRenameOperations).PendingFileRenameOperations){ $pend += 'Pending file operations' }
$o.PendingReboot = @($pend)
$o.Disks = @(Get-PhysicalDisk | ForEach-Object {
  $rc = $_ | Get-StorageReliabilityCounter
  [pscustomobject]@{ Name=[string]$_.FriendlyName; Media=[string]$_.MediaType; Bus=[string]$_.BusType; Health=[string]$_.HealthStatus;
    Op=[string](($_.OperationalStatus) -join ','); Size=[int64]$_.Size; Wear=[int]$rc.Wear; Temp=[int]$rc.Temperature;
    ReadErr=[int64]$rc.ReadErrorsUncorrected; WriteErr=[int64]$rc.WriteErrorsUncorrected; PowerOnHours=[int64]$rc.PowerOnHours }
})
$o.TrimOff = [bool]((fsutil behavior query DisableDeleteNotify) -match 'NTFS DisableDeleteNotify\s*=\s*1')
try { $mp = Get-MpComputerStatus -ErrorAction Stop; $o.DefenderRT = [bool]$mp.RealTimeProtectionEnabled; $o.SigAgeDays = [int]$mp.AntivirusSignatureAge } catch { }
$o.AV = @(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct | ForEach-Object { [pscustomobject]@{ Name=[string]$_.displayName; State=[int64]$_.productState } })
$o.Firewall = @(Get-NetFirewallProfile | ForEach-Object { [pscustomobject]@{ Name=[string]$_.Name; Enabled=[string]$_.Enabled } })
$hf = Get-HotFix | Where-Object { $_.InstalledOn } | Sort-Object InstalledOn -Descending | Select-Object -First 1
if($hf){ $o.LastUpdate = $hf.InstalledOn.ToString('o'); $o.LastKB = [string]$hf.HotFixID }
$lic = Get-CimInstance SoftwareLicensingProduct -Filter "PartialProductKey IS NOT NULL AND Name LIKE 'Windows%'" | Select-Object -First 1
$o.Licensed = if($lic){ [int]$lic.LicenseStatus } else { -1 }
$o
`

func runHealth(c *core.Ctx) (*core.Result, error) {
	r := &core.Result{Title: "System health"}
	t := core.Table{Headers: []string{"Check", "Status"}}
	if bt, err := host.BootTimeWithContext(c); err == nil {
		up := time.Since(time.Unix(int64(bt), 0))
		t.Rows = append(t.Rows, []string{"Uptime", sys.Age(up)})
		if up > 7*24*time.Hour {
			r.Add(core.Finding{ID: "uptime", Severity: core.Low, Title: "No real restart for " + sys.Age(up),
				Detail: "Memory leaks, stuck updates and driver glitches pile up. Note: 'Shut down' with Fast Startup ON is not a restart — use Restart.",
				Fixes: []core.Fix{{ID: "restart", Title: "Restart in 5 minutes", Risk: core.Moderate,
					Action: core.Action{Kind: core.ActExec, Cmd: []string{"shutdown", "/r", "/t", "300", "/c", "WinSight: restarting in 5 minutes (shutdown /a to cancel)"}}}}})
		}
	}
	var h healthPS
	err := sys.PSJSON(c, healthScript, &h)
	if windowsOnly(r, err) || err != nil {
		r.Tables = append(r.Tables, t)
		return r, nil
	}
	if len(h.PendingReboot) > 0 {
		t.Rows = append(t.Rows, []string{"Pending restart", strings.Join(h.PendingReboot, ", ")})
		r.Add(core.Finding{ID: "pending-reboot", Severity: core.Medium, Title: "A restart is pending (" + strings.Join(h.PendingReboot, ", ") + ")",
			Detail: "Updates or installers are waiting for a restart; until then fixes aren't active and new installs may fail."})
	}
	for _, d := range h.Disks {
		status := fmt.Sprintf("%s %s %s — %s", d.Media, d.Bus, sys.HumanBytes(d.Size), d.Health)
		if d.Wear > 0 {
			status += fmt.Sprintf(", wear %d%%", d.Wear)
		}
		if d.Temp > 0 {
			status += fmt.Sprintf(", %d°C", d.Temp)
		}
		if d.PowerOnHours > 0 {
			status += fmt.Sprintf(", %d h on", d.PowerOnHours)
		}
		t.Rows = append(t.Rows, []string{"Disk " + d.Name, status})
		id := "disk-" + slug(d.Name)
		switch {
		case !strings.EqualFold(d.Health, "Healthy") && d.Health != "":
			r.Add(core.Finding{ID: id + "-health", Severity: core.Critical, Title: "Disk " + d.Name + " reports " + d.Health,
				Detail: "The drive itself says it is failing or degraded. BACK UP NOW, then replace it.", Evidence: []string{d.Op}})
		case d.Wear >= 90:
			r.Add(core.Finding{ID: id + "-wear", Severity: core.High, Title: fmt.Sprintf("SSD %s is %d%% worn", d.Name, d.Wear), Detail: "Near the end of its rated write endurance. Back up and plan a replacement."})
		case d.Wear >= 70:
			r.Add(core.Finding{ID: id + "-wear", Severity: core.Low, Title: fmt.Sprintf("SSD %s is %d%% worn", d.Name, d.Wear), Detail: "Keep backups current."})
		}
		if d.ReadErr > 0 || d.WriteErr > 0 {
			r.Add(core.Finding{ID: id + "-errors", Severity: core.High, Title: fmt.Sprintf("Disk %s has uncorrected read/write errors (%d/%d)", d.Name, d.ReadErr, d.WriteErr), Detail: "Data may be silently damaged. Back up and run `chkdsk /r`."})
		}
		if d.Temp >= 70 {
			r.Add(core.Finding{ID: id + "-hot", Severity: core.Medium, Title: fmt.Sprintf("Disk %s runs hot (%d°C)", d.Name, d.Temp), Detail: "NVMe drives throttle above ~70°C. Add a heatsink or improve airflow."})
		}
		if strings.EqualFold(d.Media, "HDD") && strings.EqualFold(d.Bus, "SATA") && len(h.Disks) == 1 {
			r.Add(core.Finding{ID: id + "-hdd", Severity: core.Medium, Title: "Windows runs from a spinning hard disk",
				Detail: "An SSD is the single biggest speed upgrade possible: boot and app launch 3–10× faster. Cloning the disk takes an hour."})
		}
	}
	if h.TrimOff {
		r.Add(core.Finding{ID: "trim-off", Severity: core.Medium, Title: "TRIM is disabled",
			Detail: "Without TRIM, SSDs slow down over time and wear faster.",
			Fixes: []core.Fix{{ID: "enable", Title: "Enable TRIM", Risk: core.Safe, Admin: true,
				Action: core.Action{Kind: core.ActExec, Cmd: []string{"fsutil", "behavior", "set", "DisableDeleteNotify", "0"}}}}})
	}
	var avOn []string
	for _, a := range h.AV {
		on := a.State&0x1000 != 0
		t.Rows = append(t.Rows, []string{"Antivirus " + a.Name, onOff(on)})
		if on {
			avOn = append(avOn, a.Name)
		}
	}
	if len(avOn) == 0 && (h.DefenderRT == nil || !*h.DefenderRT) {
		r.Add(core.Finding{ID: "no-av", Severity: core.High, Title: "No active real-time antivirus",
			Detail: "Neither Microsoft Defender nor another antivirus is protecting this PC.",
			Fixes: []core.Fix{{ID: "defender-on", Title: "Turn on Defender real-time protection", Risk: core.Safe, Admin: true,
				Action: core.Action{Kind: core.ActPS, Script: "Set-MpPreference -DisableRealtimeMonitoring $false"}}}})
	}
	if len(avOn) > 1 {
		r.Add(core.Finding{ID: "multi-av", Severity: core.Medium, Title: "Several antivirus products are active: " + strings.Join(avOn, ", "),
			Detail: "Two real-time scanners fight over every file, slowing the PC a lot. Keep one."})
	}
	if h.SigAgeDays > 7 {
		r.Add(core.Finding{ID: "av-outdated", Severity: core.Medium, Title: fmt.Sprintf("Defender signatures are %d days old", h.SigAgeDays),
			Fixes: []core.Fix{{ID: "update", Title: "Update Defender signatures", Risk: core.Safe, Action: core.Action{Kind: core.ActPS, Script: "Update-MpSignature"}}}})
	}
	for _, fw := range h.Firewall {
		t.Rows = append(t.Rows, []string{"Firewall " + fw.Name, fw.Enabled})
		if !strings.EqualFold(fw.Enabled, "True") && fw.Enabled != "1" {
			r.Add(core.Finding{ID: "firewall-" + slug(fw.Name), Severity: core.High, Title: "Firewall is OFF for the " + fw.Name + " profile",
				Fixes: []core.Fix{{ID: "enable", Title: "Turn the firewall on", Risk: core.Safe, Admin: true, Reversible: true,
					Action: core.Action{Kind: core.ActPS, Script: "Set-NetFirewallProfile -Profile " + fw.Name + " -Enabled True"},
					Undo:   &core.Action{Kind: core.ActPS, Script: "Set-NetFirewallProfile -Profile " + fw.Name + " -Enabled False"}}}})
		}
	}
	if lu, err := time.Parse(time.RFC3339, h.LastUpdate); err == nil {
		age := time.Since(lu)
		t.Rows = append(t.Rows, []string{"Last update installed", fmt.Sprintf("%s (%s ago)", h.LastKB, sys.Age(age))})
		if age > 60*24*time.Hour {
			r.Add(core.Finding{ID: "updates-stale", Severity: core.Medium, Title: "No Windows update installed for " + sys.Age(age),
				Detail: "Security patches ship monthly. Run `updates` to see why updates stopped."})
		}
	}
	switch h.Licensed {
	case 1:
		t.Rows = append(t.Rows, []string{"Windows activation", "activated"})
	case -1:
	default:
		t.Rows = append(t.Rows, []string{"Windows activation", "NOT activated"})
		r.Add(core.Finding{ID: "not-activated", Severity: core.Low, Title: "Windows is not activated", Detail: "Personalisation is locked and you get nag messages. Settings › System › Activation."})
	}
	r.Tables = append(r.Tables, t)
	r.Summary = fmt.Sprintf("%d checks, %d issues.", len(t.Rows), len(r.Findings))
	return r, nil
}

type eventGroup struct {
	Provider string
	ID       int `json:"Id"`
	Level    int
	Log      string
	Count    int
	Last     string
	Msg      string
}

const eventsScript = `
$since = (Get-Date).AddDays(-DAYS)
$a = @(Get-WinEvent -FilterHashtable @{LogName='System','Application'; Level=1,2; StartTime=$since} -MaxEvents 4000)
$b = @(Get-WinEvent -FilterHashtable @{LogName='System'; Level=3; StartTime=$since; ProviderName='disk','Ntfs','storahci','stornvme','Display','nvlddmkm','Microsoft-Windows-Resource-Exhaustion-Detector','Microsoft-Windows-Kernel-Processor-Power','Microsoft-Windows-WHEA-Logger'} -MaxEvents 2000)
$g = ($a + $b) | Group-Object ProviderName, Id | ForEach-Object {
  $f = $_.Group[0]
  [pscustomobject]@{ Provider=[string]$f.ProviderName; Id=[int]$f.Id; Level=[int]$f.Level; Log=[string]$f.LogName; Count=[int]$_.Count; Last=$f.TimeCreated.ToString('o'); Msg=([string]$f.Message).Split([char]10)[0].Trim() }
}
@{ items = @($g) }
`

func runEvents(c *core.Ctx) (*core.Result, error) {
	days := c.Int("days", 7)
	r := &core.Result{Title: fmt.Sprintf("Event log errors (last %d days)", days)}
	var out struct{ Items sys.List[eventGroup] }
	err := sys.PSJSON(c, strings.Replace(eventsScript, "DAYS", strconv.Itoa(days), 1), &out)
	if windowsOnly(r, err) || err != nil {
		return r, nil
	}
	items := []eventGroup(out.Items)
	sort.Slice(items, func(i, j int) bool { return items[i].Count > items[j].Count })
	t := core.Table{Headers: []string{"Count", "Source/ID", "Meaning", "Last seen"}}
	noise := 0
	for _, e := range items {
		info, known := kb.ExplainEvent(e.Provider, e.ID)
		meaning := e.Msg
		if known {
			meaning = info.Meaning
		}
		last := e.Last
		if tm, err := time.Parse(time.RFC3339, e.Last); err == nil {
			last = sys.Age(time.Since(tm)) + " ago"
		}
		if len(t.Rows) < 30 {
			t.Rows = append(t.Rows, []string{strconv.Itoa(e.Count), fmt.Sprintf("%s/%d", e.Provider, e.ID), sys.Truncate(meaning, 70), last})
		}
		if known && info.Noise {
			noise++
			continue
		}
		sev := core.Low
		p := strings.ToLower(e.Provider)
		switch {
		case e.Level == 1 || p == "disk" || p == "ntfs" || strings.Contains(p, "whea") || p == "stornvme" || p == "storahci":
			sev = core.High
		case e.Count >= 10:
			sev = core.Medium
		}
		if !known && e.Count < 3 {
			sev = core.Info
		}
		f := core.Finding{ID: slug(fmt.Sprintf("%s-%d", e.Provider, e.ID)), Severity: sev,
			Title:    fmt.Sprintf("%s event %d ×%d", e.Provider, e.ID, e.Count),
			Detail:   meaning,
			Evidence: []string{e.Msg},
			Data:     map[string]any{"provider": e.Provider, "id": e.ID, "count": e.Count, "last": e.Last, "log": e.Log}}
		if known {
			f.Detail += " → " + info.Action
		}
		r.Add(f)
	}
	r.Tables = append(r.Tables, t)
	r.Summary = fmt.Sprintf("%d distinct error types (%d are known harmless noise).", len(items), noise)
	return r, nil
}

type crashPS struct {
	Bsod sys.List[struct {
		Time, Msg, P0 string
	}]
	PowerLoss int
	Apps      sys.List[struct {
		App, Module string
		Count       int
		Last        string
	}]
	Hangs sys.List[struct {
		App   string
		Count int
	}]
}

const crashScript = `
$o = [ordered]@{}
$o.Bsod = @(Get-WinEvent -FilterHashtable @{LogName='System'; Id=1001; ProviderName='Microsoft-Windows-WER-SystemErrorReporting'; StartTime=(Get-Date).AddDays(-90)} -MaxEvents 50 | ForEach-Object {
  [pscustomobject]@{ Time=$_.TimeCreated.ToString('o'); Msg=[string]$_.Message; P0=[string]$_.Properties[0].Value } })
$o.PowerLoss = @(Get-WinEvent -FilterHashtable @{LogName='System'; Id=41; ProviderName='Microsoft-Windows-Kernel-Power'; StartTime=(Get-Date).AddDays(-90)} -MaxEvents 200).Count
$o.Apps = @(Get-WinEvent -FilterHashtable @{LogName='Application'; Id=1000; ProviderName='Application Error'; StartTime=(Get-Date).AddDays(-30)} -MaxEvents 500 |
  ForEach-Object { [pscustomobject]@{ App=[string]$_.Properties[0].Value; Module=[string]$_.Properties[3].Value; T=$_.TimeCreated } } |
  Group-Object App, Module | ForEach-Object { [pscustomobject]@{ App=[string]$_.Group[0].App; Module=[string]$_.Group[0].Module; Count=[int]$_.Count; Last=$_.Group[0].T.ToString('o') } })
$o.Hangs = @(Get-WinEvent -FilterHashtable @{LogName='Application'; Id=1002; StartTime=(Get-Date).AddDays(-30)} -MaxEvents 300 |
  ForEach-Object { [string]$_.Properties[0].Value } | Group-Object | ForEach-Object { [pscustomobject]@{ App=$_.Name; Count=[int]$_.Count } })
$o
`

var hexCode = regexp.MustCompile(`0x[0-9a-fA-F]{8}`)

type dumpInfo struct {
	File string
	Time time.Time
	Code uint32
}

// readMinidumps reads bugcheck codes straight from kernel dump headers.
func readMinidumps() []dumpInfo {
	var out []dumpInfo
	files, _ := filepath.Glob(filepath.Join(sys.Known().Windows, "Minidump", "*.dmp"))
	for _, f := range files {
		fh, err := os.Open(f)
		if err != nil {
			continue
		}
		hdr := make([]byte, 0x40)
		n, _ := fh.Read(hdr)
		fh.Close()
		fi, _ := os.Stat(f)
		if n < 0x40 || string(hdr[:4]) != "PAGE" {
			continue
		}
		var code uint32
		if string(hdr[4:8]) == "DU64" {
			code = binary.LittleEndian.Uint32(hdr[0x38:])
		} else {
			code = binary.LittleEndian.Uint32(hdr[0x20:]) // 32-bit DUMP_HEADER32
		}
		out = append(out, dumpInfo{File: f, Time: fi.ModTime(), Code: code})
	}
	return out
}

func runCrashes(c *core.Ctx) (*core.Result, error) {
	r := &core.Result{Title: "Crashes"}
	var cp crashPS
	err := sys.PSJSON(c, crashScript, &cp)
	if windowsOnly(r, err) || err != nil {
		return r, nil
	}
	codes := map[uint32][]string{}
	tb := core.Table{Title: "Blue screens (90 days)", Headers: []string{"When", "Stop code", "Meaning"}}
	for _, b := range cp.Bsod {
		m := hexCode.FindString(b.P0)
		if m == "" {
			m = hexCode.FindString(b.Msg)
		}
		v, _ := strconv.ParseUint(strings.TrimPrefix(m, "0x"), 16, 32)
		code := uint32(v)
		name, _ := kb.ExplainBugCheck(code)
		when := b.Time
		if tm, err := time.Parse(time.RFC3339, b.Time); err == nil {
			when = tm.Format("2006-01-02 15:04")
		}
		codes[code] = append(codes[code], when)
		tb.Rows = append(tb.Rows, []string{when, fmt.Sprintf("0x%X", code), name})
	}
	dumps := readMinidumps()
	for _, d := range dumps {
		if len(cp.Bsod) == 0 {
			name, _ := kb.ExplainBugCheck(d.Code)
			tb.Rows = append(tb.Rows, []string{d.Time.Format("2006-01-02 15:04"), fmt.Sprintf("0x%X", d.Code), name})
			codes[d.Code] = append(codes[d.Code], d.Time.Format("2006-01-02"))
		}
	}
	for code, whens := range codes {
		name, advice := kb.ExplainBugCheck(code)
		sev := core.High
		if len(whens) == 1 {
			sev = core.Medium
		}
		r.Add(core.Finding{ID: fmt.Sprintf("bsod-%x", code), Severity: sev, Title: fmt.Sprintf("Blue screen %s ×%d", name, len(whens)),
			Detail: advice, Evidence: whens,
			Data: map[string]any{"stop_code": fmt.Sprintf("0x%08X", code), "analyse": "WinDbg: !analyze -v on " + filepath.Join(sys.Known().Windows, "Minidump")}})
	}
	if len(tb.Rows) > 0 {
		r.Tables = append(r.Tables, tb)
	}
	if cp.PowerLoss > 0 {
		sev := core.Low
		if cp.PowerLoss >= 3 {
			sev = core.Medium
		}
		r.Add(core.Finding{ID: "unexpected-shutdowns", Severity: sev, Title: fmt.Sprintf("%d unexpected shutdowns in 90 days", cp.PowerLoss),
			Detail: "Power cut, forced power-off, freeze or crash without a dump. Repeated ones with no blue screen point to power supply, overheating or a hard freeze."})
	}
	ta := core.Table{Title: "Crashing apps (30 days)", Headers: []string{"App", "Faulting module", "Crashes"}}
	sort.Slice(cp.Apps, func(i, j int) bool { return cp.Apps[i].Count > cp.Apps[j].Count })
	for _, a := range cp.Apps {
		ta.Rows = append(ta.Rows, []string{a.App, a.Module, strconv.Itoa(a.Count)})
		if a.Count >= 3 {
			hint := "Update or reinstall the app."
			switch strings.ToLower(a.Module) {
			case "ntdll.dll", "kernelbase.dll", "ucrtbase.dll", "msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll":
				hint = "Crash inside a Windows/C++ runtime DLL: repair the Visual C++ Redistributables (`missing`) and update the app; if many apps crash here run `sfc /scannow`."
			case "clr.dll", "coreclr.dll":
				hint = "A .NET crash: update/repair the app and the .NET runtime."
			case "nvwgf2umx.dll", "atidxx64.dll", "amdxc64.dll", "igd10iumd64.dll", "d3d11.dll", "dxgi.dll":
				hint = "Crash in the graphics driver: clean-install the latest GPU driver."
			}
			r.Add(core.Finding{ID: "app-" + slug(a.App+"-"+a.Module), Severity: core.Medium, Title: fmt.Sprintf("%s crashed %d times (in %s)", a.App, a.Count, a.Module), Detail: hint})
		}
	}
	if len(ta.Rows) > 0 {
		r.Tables = append(r.Tables, ta)
	}
	for _, hg := range cp.Hangs {
		if hg.Count >= 3 {
			r.Add(core.Finding{ID: "hang-" + slug(hg.App), Severity: core.Low, Title: fmt.Sprintf("%s froze %d times", hg.App, hg.Count),
				Detail: "Freezes usually come from a slow disk, antivirus scanning or a buggy add-in. Check `health` and update the app."})
		}
	}
	nb := len(cp.Bsod)
	if nb == 0 {
		nb = len(dumps)
	}
	r.Summary = fmt.Sprintf("%d blue screens, %d unexpected shutdowns, %d crashing apps.", nb, cp.PowerLoss, len(cp.Apps))
	adminNote(r)
	return r, nil
}

var cmErrors = map[int]string{
	1: "not configured correctly", 3: "driver corrupted or low memory", 10: "cannot start", 12: "resource conflict",
	14: "needs a restart", 18: "drivers must be reinstalled", 19: "registry configuration damaged", 22: "disabled",
	24: "not present or not working", 28: "no driver installed", 29: "disabled by firmware", 31: "not working properly",
	32: "driver start disabled", 37: "driver failed to initialise", 39: "driver missing or corrupted", 41: "driver loaded but hardware not found",
	43: "stopped: reported problems", 48: "driver blocked (known incompatible)", 52: "driver not signed",
}

const driversScript = `
$o = [ordered]@{}
$o.Bad = @(Get-CimInstance Win32_PnPEntity | Where-Object { $_.ConfigManagerErrorCode -ne 0 -and $_.ConfigManagerErrorCode -ne 45 } | ForEach-Object {
  [pscustomobject]@{ Name=[string]$_.Name; Id=[string]$_.PNPDeviceID; Code=[int]$_.ConfigManagerErrorCode; Class=[string]$_.PNPClass } })
$o.Ghost = @(Get-PnpDevice | Where-Object { -not $_.Present }).Count
$o.Drivers = @(Get-CimInstance Win32_PnPSignedDriver | Where-Object { $_.DeviceClass -in @('DISPLAY','NET','SCSIADAPTER','HDC','MEDIA','BLUETOOTH','SYSTEM') -and $_.DriverDate } | ForEach-Object {
  [pscustomobject]@{ Name=[string]$_.DeviceName; Class=[string]$_.DeviceClass; Version=[string]$_.DriverVersion; Date=$_.DriverDate.ToString('o'); Provider=[string]$_.DriverProviderName } })
$o
`

func runDrivers(c *core.Ctx) (*core.Result, error) {
	r := &core.Result{Title: "Devices & drivers"}
	var d struct {
		Bad sys.List[struct {
			Name, Class string
			ID          string `json:"Id"`
			Code        int
		}]
		Ghost   int
		Drivers sys.List[struct{ Name, Class, Version, Date, Provider string }]
	}
	err := sys.PSJSON(c, driversScript, &d)
	if windowsOnly(r, err) || err != nil {
		return r, nil
	}
	tb := core.Table{Title: "Devices with problems", Headers: []string{"Device", "Class", "Problem"}}
	for _, b := range d.Bad {
		why := cmErrors[b.Code]
		if why == "" {
			why = fmt.Sprintf("error code %d", b.Code)
		}
		tb.Rows = append(tb.Rows, []string{b.Name, b.Class, fmt.Sprintf("%s (code %d)", why, b.Code)})
		f := core.Finding{ID: "dev-" + shortHash(b.ID), Severity: core.Medium, Title: fmt.Sprintf("%s: %s", nonEmpty(b.Name, b.ID), why),
			Evidence: []string{b.ID}, Data: map[string]any{"code": b.Code, "instance_id": b.ID, "class": b.Class}}
		switch b.Code {
		case 22:
			f.Severity = core.Info
			f.Fixes = []core.Fix{{ID: "enable", Title: "Enable the device", Risk: core.Safe, Admin: true, Reversible: true,
				Action: core.Action{Kind: core.ActPS, Script: "Enable-PnpDevice -InstanceId " + sys.PSQuote(b.ID) + " -Confirm:$false"},
				Undo:   &core.Action{Kind: core.ActPS, Script: "Disable-PnpDevice -InstanceId " + sys.PSQuote(b.ID) + " -Confirm:$false"}}}
		case 14:
			f.Detail = "Restart Windows."
		default:
			f.Detail = "Reinstalling the device lets Windows pick the driver again; if it fails, download the driver from the PC/device maker (or Windows Update › Optional updates)."
			f.Fixes = []core.Fix{
				{ID: "rescan", Title: "Rescan hardware", Risk: core.Safe, Admin: true, Action: core.Action{Kind: core.ActExec, Cmd: []string{"pnputil", "/scan-devices"}}},
				{ID: "reinstall", Title: "Remove the device and let Windows reinstall it", Risk: core.Moderate, Admin: true,
					Action: core.Action{Kind: core.ActPS, Script: "pnputil /remove-device " + sys.PSQuote(b.ID) + "\nStart-Sleep 2\npnputil /scan-devices"}}}
		}
		r.Add(f)
	}
	if len(tb.Rows) > 0 {
		r.Tables = append(r.Tables, tb)
	}
	td := core.Table{Title: "Key drivers", Headers: []string{"Device", "Class", "Version", "Date", "Provider"}}
	for _, dr := range d.Drivers {
		dt, _ := time.Parse(time.RFC3339, dr.Date)
		if dr.Class == "SYSTEM" && !strings.Contains(strings.ToLower(dr.Name), "chipset") && !strings.Contains(strings.ToLower(dr.Name), "management engine") {
			continue
		}
		td.Rows = append(td.Rows, []string{dr.Name, dr.Class, dr.Version, dt.Format("2006-01-02"), dr.Provider})
		lname := strings.ToLower(dr.Name)
		if dr.Class == "DISPLAY" && strings.Contains(lname, "basic display") {
			r.Add(core.Finding{ID: "gpu-basic", Severity: core.High, Title: "No real graphics driver (Microsoft Basic Display Adapter)",
				Detail: "Without the GPU maker's driver: no acceleration, laggy windows, wrong resolution, no games. Install the NVIDIA/AMD/Intel driver.",
				Fixes:  []core.Fix{{ID: "install", Title: "Install GPU driver via Windows Update", Risk: core.Safe, Admin: true, Action: core.Action{Kind: core.ActExec, Cmd: []string{"pnputil", "/scan-devices"}}}}})
		} else if dr.Class == "DISPLAY" && !dt.IsZero() && time.Since(dt) > 400*24*time.Hour {
			r.Add(core.Finding{ID: "gpu-old-" + slug(dr.Name), Severity: core.Low, Title: fmt.Sprintf("Graphics driver is from %s", dt.Format("Jan 2006")),
				Detail: "GPU drivers bring real speed and stability fixes; update from the NVIDIA/AMD/Intel app or site."})
		}
	}
	sort.Slice(td.Rows, func(i, j int) bool { return td.Rows[i][1] < td.Rows[j][1] })
	r.Tables = append(r.Tables, td)
	if d.Ghost > 150 {
		r.Add(core.Finding{ID: "ghost-devices", Severity: core.Info, Title: fmt.Sprintf("%d ghost devices (not connected anymore)", d.Ghost),
			Detail: "Hidden leftovers of every USB stick, monitor and dock ever plugged in. Harmless but can cause COM-port or audio device confusion. View them in Device Manager › View › Show hidden devices."})
	}
	r.Summary = fmt.Sprintf("%d devices with problems, %d ghost devices.", len(d.Bad), d.Ghost)
	return r, nil
}

func nonEmpty(a, b string) string {
	if a != "" {
		return a
	}
	return b
}

var wuErrors = map[string]string{
	"0x80070643": "Install failed — for KB5034441 it means the recovery partition is too small (safe to hide that update).",
	"0x800F081F": "Source files missing: component store damaged → DISM /RestoreHealth.",
	"0x80073712": "Component store corrupted → DISM /RestoreHealth then sfc /scannow.",
	"0x800F0922": "Could not install (often VPN/proxy, or System Reserved partition full).",
	"0x80070070": "Not enough disk space.",
	"0x80070002": "Files missing in the update cache → reset Windows Update components.",
	"0x8024A105": "Update agent error → reset Windows Update components.",
	"0x80240034": "Download failed → reset Windows Update components.",
	"0x8007000D": "Corrupt update data → reset Windows Update components.",
	"0x800F0831": "A previous update package is missing → DISM /RestoreHealth.",
	"0x80070005": "Access denied (often antivirus interference).",
	"0x80244022": "Update server unreachable (network/proxy).",
}

const updatesScript = `
$s = New-Object -ComObject Microsoft.Update.Session
$srch = $s.CreateUpdateSearcher()
$n = $srch.GetTotalHistoryCount()
$o = [ordered]@{}
$o.History = @(if($n -gt 0){ $srch.QueryHistory(0, [Math]::Min($n, 60)) | Where-Object { $_.Title } | ForEach-Object {
  [pscustomobject]@{ Title=[string]$_.Title; Date=$_.Date.ToString('o'); Result=[int]$_.ResultCode; HResult=('0x{0:X8}' -f $_.HResult) } } })
$o.Start = [string](Get-Service wuauserv).StartType
$o.Paused = [string](Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings' -Name PauseUpdatesExpiryTime).PauseUpdatesExpiryTime
if(CHECK){ $res = $srch.Search("IsInstalled=0 and IsHidden=0"); $o.Pending = @($res.Updates | ForEach-Object { [string]$_.Title }) }
$o
`

const resetWU = `$svc = 'wuauserv','bits','cryptsvc','msiserver'
Stop-Service $svc -Force
$sd = "$env:SystemRoot\SoftwareDistribution"; $cr = "$env:SystemRoot\System32\catroot2"
if(Test-Path "$sd.old"){ Remove-Item "$sd.old" -Recurse -Force }
if(Test-Path "$cr.old"){ Remove-Item "$cr.old" -Recurse -Force }
Rename-Item $sd "$sd.old" -Force
Rename-Item $cr "$cr.old" -Force
Start-Service $svc
"Windows Update components reset. Check for updates again."`

func runUpdates(c *core.Ctx) (*core.Result, error) {
	r := &core.Result{Title: "Windows Update"}
	check := "$false"
	if c.Bool("check") {
		check = "$true"
		c.Progress("asking Windows Update for pending updates (can take a minute)…")
	}
	var u struct {
		History sys.List[struct {
			Title, Date, HResult string
			Result               int
		}]
		Start, Paused string
		Pending       sys.List[string]
	}
	err := sys.PSJSON(c, strings.Replace(updatesScript, "CHECK", check, 1), &u)
	if windowsOnly(r, err) || err != nil {
		return r, nil
	}
	t := core.Table{Title: "Recent update history", Headers: []string{"Date", "Result", "Update", "Code"}}
	fails := map[string][]string{}
	for i, h := range u.History {
		dt, _ := time.Parse(time.RFC3339, h.Date)
		res := map[int]string{1: "in progress", 2: "ok", 3: "ok (with errors)", 4: "FAILED", 5: "aborted"}[h.Result]
		if i < 20 {
			t.Rows = append(t.Rows, []string{dt.Format("2006-01-02"), res, sys.Truncate(h.Title, 70), map[bool]string{true: h.HResult}[h.Result >= 4]})
		}
		if h.Result == 4 && time.Since(dt) < 45*24*time.Hour {
			fails[h.HResult] = append(fails[h.HResult], h.Title)
		}
	}
	r.Tables = append(r.Tables, t)
	for code, titles := range fails {
		why := wuErrors["0x"+strings.ToUpper(strings.TrimPrefix(strings.ToLower(code), "0x"))]
		if why == "" {
			why = "Update installation failed with " + code + "."
		}
		f := core.Finding{ID: "fail-" + strings.ToLower(code), Severity: core.Medium, Title: fmt.Sprintf("%d failed update installs (%s)", len(titles), code),
			Detail: why, Evidence: titles}
		f.Fixes = []core.Fix{
			{ID: "reset", Title: "Reset Windows Update components", Risk: core.Safe, Admin: true, Action: core.Action{Kind: core.ActPS, Script: resetWU}},
			{ID: "dism", Title: "Repair the component store (DISM RestoreHealth)", Risk: core.Safe, Admin: true,
				Action: core.Action{Kind: core.ActExec, Cmd: []string{"Dism.exe", "/Online", "/Cleanup-Image", "/RestoreHealth"}}},
		}
		r.Add(f)
	}
	if strings.EqualFold(u.Start, "Disabled") {
		r.Add(core.Finding{ID: "wu-disabled", Severity: core.High, Title: "Windows Update service is disabled",
			Detail: "No security patches will install.",
			Fixes: []core.Fix{{ID: "enable", Title: "Re-enable Windows Update", Risk: core.Safe, Admin: true, Reversible: true,
				Action: core.Action{Kind: core.ActPS, Script: serviceStartScript("wuauserv", 3, "start")},
				Undo:   &core.Action{Kind: core.ActPS, Script: serviceStartScript("wuauserv", 4, "stop")}}}})
	}
	if pt, err := time.Parse(time.RFC3339, u.Paused); err == nil && pt.After(time.Now()) {
		r.Add(core.Finding{ID: "paused", Severity: core.Low, Title: "Updates are paused until " + pt.Format("2006-01-02"),
			Detail: "Security fixes are on hold. Resume in Settings › Windows Update."})
	}
	if len(u.Pending) > 0 {
		r.Add(core.Finding{ID: "pending", Severity: core.Low, Title: fmt.Sprintf("%d updates waiting to install", len(u.Pending)), Evidence: u.Pending})
	}
	r.Summary = fmt.Sprintf("%d history entries, %d failure codes in the last 45 days.", len(u.History), len(fails))
	return r, nil
}

const integrityScript = `
$o = [ordered]@{}
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$o.Admin = $admin
$o.Dirty = @(Get-CimInstance Win32_Volume | Where-Object { $_.DirtyBitSet -and $_.DriveLetter } | ForEach-Object { [string]$_.DriveLetter })
if($admin){ $o.Dism = (Dism.exe /Online /Cleanup-Image /CheckHealth /English) -join "` + "`n" + `" }
$o.LastSfc = ''
$cbs = "$env:SystemRoot\Logs\CBS\CBS.log"
if(Test-Path $cbs){ $l = Select-String -Path $cbs -Pattern '\[SR\] (Repairing|Cannot repair|Verify complete)' | Select-Object -Last 3; $o.LastSfc = ($l | ForEach-Object { $_.Line }) -join "` + "`n" + `" }
$o
`

func runIntegrity(c *core.Ctx) (*core.Result, error) {
	r := &core.Result{Title: "System integrity"}
	var in struct {
		Admin   bool
		Dirty   sys.List[string]
		Dism    string
		LastSfc string
	}
	err := sys.PSJSON(c, integrityScript, &in)
	if windowsOnly(r, err) || err != nil {
		return r, nil
	}
	sfc := core.Fix{ID: "sfc", Title: "Scan and repair system files (sfc /scannow)", Risk: core.Safe, Admin: true,
		Action: core.Action{Kind: core.ActExec, Cmd: []string{"sfc", "/scannow"}}}
	dism := core.Fix{ID: "dism", Title: "Repair the component store (DISM /RestoreHealth)", Risk: core.Safe, Admin: true,
		Action: core.Action{Kind: core.ActExec, Cmd: []string{"Dism.exe", "/Online", "/Cleanup-Image", "/RestoreHealth"}}}
	for _, d := range in.Dirty {
		r.Add(core.Finding{ID: "dirty-" + slug(d), Severity: core.High, Title: "Drive " + d + " is marked dirty (file system errors)",
			Detail: "Windows noticed file system inconsistencies. chkdsk will repair them at the next restart.",
			Fixes: []core.Fix{{ID: "chkdsk", Title: "Schedule chkdsk at next restart", Risk: core.Safe, Admin: true,
				Action: core.Action{Kind: core.ActPS, Script: "echo Y | chkdsk " + d + " /f"}}}})
	}
	switch {
	case !in.Admin:
		r.Add(core.Finding{ID: "check", Severity: core.Info, Title: "System files not verified (needs administrator)",
			Detail: "Run WinSight as administrator to check the component store, or just run the repairs below — they're safe.", Fixes: []core.Fix{dism, sfc}})
	case strings.Contains(in.Dism, "No component store corruption detected"):
		r.Notes = append(r.Notes, "Component store: healthy.")
	case strings.Contains(in.Dism, "repairable"):
		r.Add(core.Finding{ID: "component-store", Severity: core.High, Title: "Windows component store is damaged (repairable)",
			Detail: "Damaged system components cause failed updates and random errors. Run DISM, then sfc.", Evidence: []string{in.Dism}, Fixes: []core.Fix{dism, sfc}})
	default:
		if in.Dism != "" {
			r.Notes = append(r.Notes, "DISM said: "+sys.Truncate(strings.TrimSpace(in.Dism), 300))
		}
	}
	if strings.Contains(in.LastSfc, "Cannot repair") {
		r.Add(core.Finding{ID: "sfc-unrepaired", Severity: core.High, Title: "The last sfc run found files it could not repair",
			Detail: "Run DISM /RestoreHealth first (it fetches good copies), then sfc again.", Evidence: []string{in.LastSfc}, Fixes: []core.Fix{dism, sfc}})
	}
	r.Summary = fmt.Sprintf("%d problems found.", len(r.Findings))
	return r, nil
}

type netPS struct {
	Adapters sys.List[struct {
		Name, Desc, Speed, Media string
		Index                    int
	}]
	Dns sys.List[struct {
		Index   int
		Alias   string
		Servers sys.List[string]
	}]
	Gateway      string
	GatewayIndex int
	Ping         sys.List[struct {
		Host string
		Ms   sys.List[int]
	}]
	ProxyEnable int
	ProxyServer string
	AutoConfig  string
	WifiSignal  string
}

const netScript = `
$o = [ordered]@{}
$o.Adapters = @(Get-NetAdapter | Where-Object Status -eq 'Up' | ForEach-Object { [pscustomobject]@{ Name=[string]$_.Name; Desc=[string]$_.InterfaceDescription; Speed=[string]$_.LinkSpeed; Index=[int]$_.ifIndex; Media=[string]$_.PhysicalMediaType } })
$o.Dns = @(Get-DnsClientServerAddress -AddressFamily IPv4 | Where-Object { $_.ServerAddresses } | ForEach-Object { [pscustomobject]@{ Index=[int]$_.InterfaceIndex; Alias=[string]$_.InterfaceAlias; Servers=@($_.ServerAddresses) } })
$gw = Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Sort-Object RouteMetric | Select-Object -First 1
$o.Gateway = [string]$gw.NextHop; $o.GatewayIndex = [int]$gw.ifIndex
$p = New-Object System.Net.NetworkInformation.Ping
$o.Ping = @(foreach($h in @($o.Gateway,'1.1.1.1','8.8.8.8')){ if(-not $h){ continue }; $t = @(); for($i=0; $i -lt 4; $i++){ try { $r = $p.Send($h, 1000); if($r.Status -eq 'Success'){ $t += [int]$r.RoundtripTime } else { $t += -1 } } catch { $t += -1 } }; [pscustomobject]@{ Host=$h; Ms=@($t) } })
$is = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
$o.ProxyEnable = [int]$is.ProxyEnable; $o.ProxyServer = [string]$is.ProxyServer; $o.AutoConfig = [string]$is.AutoConfigURL
$w = netsh wlan show interfaces | Select-String '(\d+)%' | Select-Object -First 1
if($w){ $o.WifiSignal = $w.Matches[0].Groups[1].Value }
$o
`

// dnsBench times lookups of random names (forcing a real upstream query).
func dnsBench(ctx context.Context, server string) time.Duration {
	res := net.DefaultResolver
	if server != "" {
		res = &net.Resolver{PreferGo: true, Dial: func(ctx context.Context, _, _ string) (net.Conn, error) {
			d := net.Dialer{Timeout: 2 * time.Second}
			return d.DialContext(ctx, "udp", net.JoinHostPort(server, "53"))
		}}
	}
	var total time.Duration
	n := 0
	for _, dom := range []string{"microsoft.com", "google.com", "cloudflare.com"} {
		name := fmt.Sprintf("ws%d.%s", rand.Intn(1_000_000), dom)
		cctx, cancel := context.WithTimeout(ctx, 3*time.Second)
		start := time.Now()
		_, _ = res.LookupHost(cctx, name)
		cancel()
		total += time.Since(start)
		n++
	}
	return total / time.Duration(n)
}

var suspiciousHosts = regexp.MustCompile(`(?i)(microsoft|windowsupdate|google|bing|facebook|apple|paypal|bank|amazon|kaspersky|avast|avg|eset|malwarebytes|norton|symantec|mcafee|bitdefender|virustotal|sophos|trendmicro)`)

func runNet(c *core.Ctx) (*core.Result, error) {
	r := &core.Result{Title: "Network"}
	t := core.Table{Headers: []string{"Check", "Result"}}
	c.Progress("benchmarking DNS")
	cur := dnsBench(c, "")
	cf := dnsBench(c, "1.1.1.1")
	gg := dnsBench(c, "8.8.8.8")
	t.Rows = append(t.Rows, []string{"DNS (your current)", cur.Round(time.Millisecond).String()},
		[]string{"DNS 1.1.1.1 (Cloudflare)", cf.Round(time.Millisecond).String()},
		[]string{"DNS 8.8.8.8 (Google)", gg.Round(time.Millisecond).String()})
	var n netPS
	err := sys.PSJSON(c, netScript, &n)
	if err == nil {
		for _, a := range n.Adapters {
			t.Rows = append(t.Rows, []string{"Adapter " + a.Name, a.Desc + " @ " + a.Speed})
		}
		for _, p := range n.Ping {
			ok, sum, lost := 0, 0, 0
			for _, ms := range p.Ms {
				if ms < 0 {
					lost++
				} else {
					ok++
					sum += ms
				}
			}
			label := "Ping " + p.Host
			if p.Host == n.Gateway {
				label = "Ping router " + p.Host
			}
			if ok == 0 {
				t.Rows = append(t.Rows, []string{label, "no reply"})
				continue
			}
			t.Rows = append(t.Rows, []string{label, fmt.Sprintf("%d ms avg, %d/%d lost", sum/ok, lost, len(p.Ms))})
			if p.Host == n.Gateway && sum/ok > 20 {
				r.Add(core.Finding{ID: "router-latency", Severity: core.Medium, Title: fmt.Sprintf("Slow link to your router (%d ms)", sum/ok),
					Detail: "Anything above a few ms to the router means weak Wi-Fi or interference. Move closer, use 5 GHz, or use a cable."})
			}
			if lost > 0 && p.Host != n.Gateway {
				r.Add(core.Finding{ID: "packet-loss-" + slug(p.Host), Severity: core.Low, Title: fmt.Sprintf("Packet loss to %s (%d/%d)", p.Host, lost, len(p.Ms)),
					Detail: "Causes lag in calls and games. Check Wi-Fi signal; if wired, it's the provider."})
			}
		}
		if n.WifiSignal != "" {
			t.Rows = append(t.Rows, []string{"Wi-Fi signal", n.WifiSignal + "%"})
			if s, _ := strconv.Atoi(n.WifiSignal); s > 0 && s < 50 {
				r.Add(core.Finding{ID: "wifi-weak", Severity: core.Low, Title: "Weak Wi-Fi signal (" + n.WifiSignal + "%)", Detail: "Expect slow and unstable internet. Move closer to the router or add a mesh point."})
			}
		}
		if n.ProxyEnable == 1 || n.AutoConfig != "" {
			r.Add(core.Finding{ID: "proxy", Severity: core.Medium, Title: "A proxy is configured: " + nonEmpty(n.ProxyServer, n.AutoConfig),
				Detail: "All web traffic goes through this proxy. If you didn't set it (or it's not your company's), adware may have — it can read and slow your browsing.",
				Fixes: []core.Fix{{ID: "disable", Title: "Turn the proxy off", Risk: core.Moderate, Reversible: true,
					Action: core.Action{Kind: core.ActPS, Script: regSetScript(`HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings`, "ProxyEnable", "DWord", "0") + "\nRemove-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings' -Name AutoConfigURL -Force"},
					Undo:   &core.Action{Kind: core.ActPS, Script: regSetScript(`HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings`, "ProxyEnable", "DWord", strconv.Itoa(n.ProxyEnable)) + "\n" + regSetScript(`HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings`, "AutoConfigURL", "String", n.AutoConfig)}}}})
		}
	} else if !windowsOnly(r, err) {
		r.Errf("%v", err)
	}
	best, bestName := cf, "1.1.1.1,1.0.0.1"
	if gg < cf {
		best, bestName = gg, "8.8.8.8,8.8.4.4"
	}
	if cur > 120*time.Millisecond && cur > best*2 && len(n.Dns) > 0 && n.GatewayIndex > 0 {
		var prev []string
		for _, d := range n.Dns {
			if d.Index == n.GatewayIndex {
				prev = d.Servers
			}
		}
		undo := fmt.Sprintf("Set-DnsClientServerAddress -InterfaceIndex %d -ResetServerAddresses", n.GatewayIndex)
		if len(prev) > 0 && !(len(prev) == 1 && prev[0] == n.Gateway) {
			undo = fmt.Sprintf("Set-DnsClientServerAddress -InterfaceIndex %d -ServerAddresses %s", n.GatewayIndex, sys.PSArray(prev))
		}
		r.Add(core.Finding{ID: "dns-slow", Severity: core.Low, Title: fmt.Sprintf("Your DNS is slow (%s vs %s)", cur.Round(time.Millisecond), best.Round(time.Millisecond)),
			Detail: "Every new website waits for DNS first. A faster public DNS makes browsing feel snappier.",
			Fixes: []core.Fix{{ID: "switch", Title: "Use " + bestName + " on the active adapter", Risk: core.Moderate, Admin: true, Reversible: true,
				Action: core.Action{Kind: core.ActPS, Script: fmt.Sprintf("Set-DnsClientServerAddress -InterfaceIndex %d -ServerAddresses %s\nClear-DnsClientCache", n.GatewayIndex, sys.PSArray(strings.Split(bestName, ",")))},
				Undo:   &core.Action{Kind: core.ActPS, Script: undo}}}})
	}
	hostsFindings(r, t)
	r.Tables = append(r.Tables, t)
	r.Summary = fmt.Sprintf("DNS %s; %d issues.", cur.Round(time.Millisecond), len(r.Findings))
	return r, nil
}

func hostsFindings(r *core.Result, t core.Table) {
	hosts := filepath.Join(sys.Known().Windows, "System32", "drivers", "etc", "hosts")
	b, err := os.ReadFile(hosts)
	if err != nil {
		return
	}
	var entries, bad []string
	for _, line := range strings.Split(string(b), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		entries = append(entries, line)
		f := strings.Fields(line)
		if len(f) >= 2 && suspiciousHosts.MatchString(strings.Join(f[1:], " ")) {
			bad = append(bad, line)
		}
	}
	if len(bad) > 0 {
		r.Add(core.Finding{ID: "hosts-hijack", Severity: core.High, Title: fmt.Sprintf("hosts file redirects or blocks %d important sites", len(bad)),
			Detail:   "Malware edits the hosts file to block antivirus/Windows updates or to send you to fake sites. Unless you added these yourself, remove them.",
			Evidence: bad,
			Fixes: []core.Fix{{ID: "clean", Title: "Comment out the suspicious lines (backup kept)", Risk: core.Moderate, Admin: true, Reversible: true,
				Action: core.Action{Kind: core.ActPS, Script: fmt.Sprintf("$h = %s\nCopy-Item $h \"$h.winsight.bak\" -Force\n$bad = %s\n(Get-Content $h) | ForEach-Object { if($bad -contains $_.Trim()){ '# [WinSight] ' + $_ } else { $_ } } | Set-Content $h -Encoding ASCII\nipconfig /flushdns | Out-Null", sys.PSQuote(hosts), sys.PSArray(bad))},
				Undo:   &core.Action{Kind: core.ActPS, Script: fmt.Sprintf("Copy-Item (%s + '.winsight.bak') %s -Force", sys.PSQuote(hosts), sys.PSQuote(hosts))}}}})
	} else if len(entries) > 0 {
		r.Add(core.Finding{ID: "hosts-entries", Severity: core.Info, Title: fmt.Sprintf("hosts file has %d custom entries", len(entries)), Evidence: entries})
	}
}

type bootPS struct {
	Items sys.List[struct {
		ID                                 int `json:"Id"`
		Time, BootTime, MainPath, PostBoot string
		Name, File, Total, Degr            string
	}]
	Post int64
}

const bootScript = `
$ev = Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-Diagnostics-Performance/Operational'; Id=100,101,102,103,109; StartTime=(Get-Date).AddDays(-60)} -MaxEvents 400
$items = @($ev | ForEach-Object {
  $x = [xml]$_.ToXml(); $d = @{}
  foreach($n in $x.Event.EventData.Data){ $d[[string]$n.Name] = [string]$n.'#text' }
  [pscustomobject]@{ Id=[int]$_.Id; Time=$_.TimeCreated.ToString('o'); BootTime=$d['BootTime']; MainPath=$d['MainPathBootTime']; PostBoot=$d['BootPostBootTime'];
    Name=$d['FriendlyName']; File=$d['Name']; Total=$d['TotalTime']; Degr=$d['DegradationTime'] }
})
$post = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' -Name FwPOSTTime).FwPOSTTime
@{ items = $items; post = [int64]$post }
`

func runBoot(c *core.Ctx) (*core.Result, error) {
	r := &core.Result{Title: "Boot performance"}
	var b bootPS
	err := sys.PSJSON(c, bootScript, &b)
	if windowsOnly(r, err) || err != nil {
		return r, nil
	}
	atoi := func(s string) int64 { n, _ := strconv.ParseInt(s, 10, 64); return n }
	t := core.Table{Title: "Recent boots", Headers: []string{"When", "Total", "Until desktop", "After desktop"}}
	var sum, count int64
	type off struct {
		kind, name string
		n          int
		degr       int64
	}
	offenders := map[string]*off{}
	kinds := map[int]string{101: "app", 102: "driver", 103: "service", 109: "device"}
	for _, it := range b.Items {
		if it.ID == 100 {
			total := atoi(it.BootTime)
			if total <= 0 {
				continue
			}
			sum += total
			count++
			if len(t.Rows) < 8 {
				when := it.Time
				if tm, err := time.Parse(time.RFC3339, it.Time); err == nil {
					when = tm.Format("2006-01-02 15:04")
				}
				t.Rows = append(t.Rows, []string{when, fmt.Sprintf("%.1fs", float64(total)/1000), fmt.Sprintf("%.1fs", float64(atoi(it.MainPath))/1000), fmt.Sprintf("%.1fs", float64(atoi(it.PostBoot))/1000)})
			}
			continue
		}
		name := nonEmpty(it.Name, it.File)
		key := kinds[it.ID] + ":" + strings.ToLower(name)
		o := offenders[key]
		if o == nil {
			o = &off{kind: kinds[it.ID], name: name}
			offenders[key] = o
		}
		o.n++
		o.degr += atoi(it.Degr)
	}
	if len(t.Rows) > 0 {
		r.Tables = append(r.Tables, t)
	}
	if b.Post > 0 {
		r.Notes = append(r.Notes, fmt.Sprintf("Last BIOS/UEFI time (before Windows even starts): %.1fs.", float64(b.Post)/1000))
		if b.Post > 15000 {
			r.Add(core.Finding{ID: "slow-bios", Severity: core.Low, Title: fmt.Sprintf("Firmware (BIOS) takes %.0fs before Windows starts", float64(b.Post)/1000),
				Detail: "Enable 'Fast Boot' in the BIOS, disable unused boot devices/network boot, and remove USB drives at boot."})
		}
	}
	if count > 0 {
		avg := float64(sum) / float64(count) / 1000
		r.Summary = fmt.Sprintf("Average boot %.0fs over %d boots.", avg, count)
		if avg > 60 {
			r.Add(core.Finding{ID: "slow-boot", Severity: core.Medium, Title: fmt.Sprintf("Boot takes %.0fs on average", avg),
				Detail: "A healthy SSD-based PC boots in 15–30s. The culprits Windows itself measured are listed below; `startup` removes auto-start apps."})
		}
	} else {
		r.Summary = "No boot records readable."
		adminNote(r)
	}
	list := make([]*off, 0, len(offenders))
	for _, o := range offenders {
		list = append(list, o)
	}
	sort.Slice(list, func(i, j int) bool { return list[i].degr > list[j].degr })
	to := core.Table{Title: "What Windows says slowed boot", Headers: []string{"Type", "Name", "Times", "Delay added"}}
	for i, o := range list {
		to.Rows = append(to.Rows, []string{o.kind, o.name, strconv.Itoa(o.n), fmt.Sprintf("%.1fs", float64(o.degr)/1000)})
		if i < 8 && o.n >= 2 {
			r.Add(core.Finding{ID: "slow-" + o.kind + "-" + slug(o.name), Severity: core.Low, Title: fmt.Sprintf("%s %s slowed boot %d times (+%.1fs total)", o.kind, o.name, o.n, float64(o.degr)/1000),
				Detail: map[string]string{"app": "Disable it in `startup` if you don't need it right at sign-in.", "driver": "Update the driver (`drivers`) or the software it belongs to.",
					"service": "If it's third-party, set it to Automatic (Delayed Start) or Manual (`services`).", "device": "Update the device's driver."}[o.kind]})
		}
	}
	if len(to.Rows) > 0 {
		r.Tables = append(r.Tables, to)
	}
	return r, nil
}
