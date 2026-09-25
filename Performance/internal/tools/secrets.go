package tools

import (
	"encoding/json"
	"fmt"
	"runtime"
	"strings"
	"time"

	"github.com/shirou/gopsutil/v4/cpu"
	"github.com/shirou/gopsutil/v4/disk"
	"github.com/shirou/gopsutil/v4/host"
	"github.com/shirou/gopsutil/v4/mem"

	"github.com/coolman1984/performance/internal/core"
	"github.com/coolman1984/performance/internal/kb"
	"github.com/coolman1984/performance/internal/sys"
)

func init() {
	core.Register(&core.Tool{Name: "tweaks", Category: "secrets", InBrief: true,
		Short:    "Hidden registry settings for speed, privacy and security, compared with recommended values",
		Params:   []core.Param{{Name: "group", Desc: "only one group: speed | privacy | security | health | dev"}},
		Keywords: []string{"tweak", "tweaks", "registry", "privacy", "telemetry", "ads", "optimize", "تحسين", "خصوصية", "اعلانات", "ريجستري"},
		Run:      runTweaks})
	core.Register(&core.Tool{Name: "secrets", Category: "secrets", InBrief: true,
		Short:    "What Windows doesn't tell you: install age, BIOS age, VBS gaming cost, SMB1, BitLocker, Storage Sense, God Mode…",
		Aliases:  []string{"hidden"},
		Keywords: []string{"secret", "secrets", "hidden", "know", "اسرار", "أسرار", "مخفي", "خفايا"},
		Run:      runSecrets})
	core.Register(&core.Tool{Name: "sysinfo", Category: "secrets",
		Short:    "Quick hardware and OS snapshot (the context an agent needs first)",
		Aliases:  []string{"info", "specs"},
		Keywords: []string{"specs", "info", "hardware", "system", "مواصفات", "معلومات"},
		Run:      runSysinfo})
}

func runTweaks(c *core.Ctx) (*core.Result, error) {
	r := &core.Result{Title: "Hidden settings"}
	group := c.Str("group", "")
	if group == "" && len(c.Pos) > 0 {
		group = c.Pos[0]
	}
	type q struct{ ID, Key, Name string }
	var list []q
	var tweaks []kb.Tweak
	for _, t := range kb.Tweaks {
		if group != "" && !strings.EqualFold(group, t.Group) {
			continue
		}
		list = append(list, q{t.ID, t.Key, t.Name})
		tweaks = append(tweaks, t)
	}
	js, _ := json.Marshal(list)
	script := `$list = ConvertFrom-Json ` + sys.PSQuote(string(js)) + `
@{ items = @(foreach($t in $list){
  $k = Get-Item -Path $t.Key -ErrorAction SilentlyContinue
  $v = if($k){ $k.GetValue($t.Name, $null) } else { $null }
  if($null -ne $v){ [pscustomobject]@{ ID=$t.ID; Exists=$true; Value=[string]$v } } else { [pscustomobject]@{ ID=$t.ID; Exists=$false; Value='' } }
}) }`
	var out struct {
		Items sys.List[struct {
			ID     string
			Exists bool
			Value  string
		}]
	}
	if err := sys.PSJSON(c, script, &out); windowsOnly(r, err) || err != nil {
		return r, nil
	}
	type state struct {
		exists bool
		value  string
	}
	cur := map[string]state{}
	for _, it := range out.Items {
		cur[it.ID] = state{it.Exists, it.Value}
	}
	t := core.Table{Headers: []string{"", "Setting", "Group", "Now", "Recommended"}}
	good := 0
	for _, tw := range tweaks {
		st := cur[tw.ID]
		eff := tw.Missing
		if st.exists {
			eff = st.value
		}
		ok := eff == tw.Want
		mark := "✓"
		if !ok {
			mark = "•"
		} else {
			good++
		}
		t.Rows = append(t.Rows, []string{mark, tw.Title, tw.Group, eff, tw.Want})
		if ok {
			continue
		}
		detail := tw.Why
		if tw.After != "" {
			detail += " (Takes effect after: " + strings.ToLower(tw.After) + ".)"
		}
		r.Add(core.Finding{ID: tw.ID, Severity: tw.Sev, Title: tw.Title, Detail: detail,
			Data: map[string]any{"key": tw.Key, "name": tw.Name, "current": eff, "recommended": tw.Want, "group": tw.Group},
			Fixes: []core.Fix{{ID: "apply", Title: "Apply: " + tw.Title, Risk: tw.Risk, Admin: strings.HasPrefix(tw.Key, "HKLM"), Reversible: true,
				Action: core.Action{Kind: core.ActPS, Script: regSetScript(tw.Key, tw.Name, tw.Type, tw.Want)},
				Undo:   &core.Action{Kind: core.ActPS, Script: regRestoreScript(tw.Key, tw.Name, tw.Type, st.value, st.exists)},
				Verify: fmt.Sprintf("(Get-ItemProperty -Path %s -Name %s).%s", sys.PSQuote(tw.Key), sys.PSQuote(tw.Name), tw.Name)}}})
	}
	r.Tables = append(r.Tables, t)
	r.Summary = fmt.Sprintf("%d/%d settings already at the recommended value.", good, len(tweaks))
	r.Notes = append(r.Notes, "Apply every recommended setting at once with: fix \"tweaks.*\"  (each one is reversible with `undo`).")
	return r, nil
}

type secretsPS struct {
	Edition, Display, Build, Installed, Bios, BiosDate, Model, Cpu string
	Cores, Threads                                                 int
	Gpus                                                           sys.List[struct {
		Name, Driver, Date string
		VramMB             int64
	}]
	VBS          int
	HVCI         bool
	SecureBoot   *bool
	Tpm          string
	Smb1         *bool
	StorageSense int
	BitLocker    int
	Hags         int
}

const secretsScript = `
$o = [ordered]@{}
$os = Get-CimInstance Win32_OperatingSystem
$cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$o.Edition = [string]$os.Caption; $o.Display = [string]$cv.DisplayVersion; $o.Build = "$($cv.CurrentBuild).$($cv.UBR)"
$o.Installed = $os.InstallDate.ToString('o')
$b = Get-CimInstance Win32_BIOS; $o.Bios = "$($b.Manufacturer) $($b.SMBIOSBIOSVersion)"; if($b.ReleaseDate){ $o.BiosDate = $b.ReleaseDate.ToString('o') }
$cs = Get-CimInstance Win32_ComputerSystem; $o.Model = "$($cs.Manufacturer) $($cs.Model)"
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1; $o.Cpu = ([string]$cpu.Name).Trim(); $o.Cores = [int]$cpu.NumberOfCores; $o.Threads = [int]$cpu.NumberOfLogicalProcessors
$o.Gpus = @(Get-CimInstance Win32_VideoController | ForEach-Object { [pscustomobject]@{ Name=[string]$_.Name; Driver=[string]$_.DriverVersion; Date=$(if($_.DriverDate){$_.DriverDate.ToString('o')}else{''}); VramMB=[int64]($_.AdapterRAM/1MB) } })
$dg = Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard
$o.VBS = [int]$dg.VirtualizationBasedSecurityStatus; $o.HVCI = [bool](@($dg.SecurityServicesRunning) -contains 2)
try { $o.SecureBoot = [bool](Confirm-SecureBootUEFI -ErrorAction Stop) } catch { }
$tpm = Get-CimInstance -Namespace root\cimv2\Security\MicrosoftTpm -ClassName Win32_Tpm; $o.Tpm = if($tpm){ [string]$tpm.SpecVersion } else { '' }
try { $o.Smb1 = [bool](Get-SmbServerConfiguration -ErrorAction Stop).EnableSMB1Protocol } catch { }
$o.StorageSense = [int](Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\StorageSense\Parameters\StoragePolicy' -Name '01').'01'
$bl = Get-CimInstance -Namespace root\cimv2\Security\MicrosoftVolumeEncryption -ClassName Win32_EncryptableVolume -Filter "DriveLetter='$env:SystemDrive'"
$o.BitLocker = if($bl){ [int]$bl.ProtectionStatus } else { -1 }
$o.Hags = [int](Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers' -Name HwSchMode).HwSchMode
$o
`

func runSecrets(c *core.Ctx) (*core.Result, error) {
	r := &core.Result{Title: "Windows secrets"}
	var s secretsPS
	if err := sys.PSJSON(c, secretsScript, &s); windowsOnly(r, err) || err != nil {
		return r, nil
	}
	t := core.Table{Headers: []string{"Fact", "Value"}}
	t.Rows = append(t.Rows, []string{"Windows", fmt.Sprintf("%s %s (build %s)", s.Edition, s.Display, s.Build)}, []string{"PC", s.Model},
		[]string{"CPU", fmt.Sprintf("%s — %d cores / %d threads", s.Cpu, s.Cores, s.Threads)})
	for _, g := range s.Gpus {
		dt, _ := time.Parse(time.RFC3339, g.Date)
		t.Rows = append(t.Rows, []string{"GPU", fmt.Sprintf("%s — driver %s (%s)", g.Name, g.Driver, dt.Format("Jan 2006"))})
	}
	if it, err := time.Parse(time.RFC3339, s.Installed); err == nil {
		age := time.Since(it)
		t.Rows = append(t.Rows, []string{"Windows installed", fmt.Sprintf("%s (%.1f years ago)", it.Format("2006-01-02"), age.Hours()/24/365)})
		if age > 4*365*24*time.Hour {
			r.Add(core.Finding{ID: "old-install", Severity: core.Info, Title: fmt.Sprintf("This Windows installation is %.0f years old", age.Hours()/24/365),
				Detail: "Years of installs/uninstalls leave drivers, services and registry cruft behind. If the PC stays slow after all fixes, a clean reinstall (Settings › Recovery › Reset this PC › Keep my files) is the nuclear option."})
		}
	}
	if bd, err := time.Parse(time.RFC3339, s.BiosDate); err == nil {
		t.Rows = append(t.Rows, []string{"BIOS/UEFI", fmt.Sprintf("%s (%s)", s.Bios, bd.Format("2006-01-02"))})
		if time.Since(bd) > 3*365*24*time.Hour {
			r.Add(core.Finding{ID: "old-bios", Severity: core.Low, Title: "BIOS is from " + bd.Format("2006"),
				Detail: "BIOS updates fix stability, sleep/wake, RAM compatibility and CPU security bugs. Get it from your PC/motherboard maker's support page (don't interrupt it while flashing)."})
		}
	}
	if s.VBS == 2 {
		t.Rows = append(t.Rows, []string{"Virtualization-based security", "running" + map[bool]string{true: " (Memory Integrity ON)"}[s.HVCI]})
		r.Add(core.Finding{ID: "vbs", Severity: core.Info, Title: "Virtualization-based security is on",
			Detail: "It blocks a whole class of kernel malware, but Microsoft confirms it can cost 5–15% FPS in some games. Keep it on unless you game competitively; toggle in Windows Security › Device security › Core isolation.",
			Fixes:  []core.Fix{{ID: "toggle", Title: "Turn Memory Integrity off (gaming PCs only)", Risk: core.Risky, Action: core.Action{Kind: core.ActManual, Manual: "Windows Security › Device security › Core isolation details › Memory integrity: Off, then restart."}}}})
	}
	if s.SecureBoot != nil {
		t.Rows = append(t.Rows, []string{"Secure Boot", onOff(*s.SecureBoot)})
	}
	if s.Tpm != "" {
		t.Rows = append(t.Rows, []string{"TPM", s.Tpm})
	}
	if s.Smb1 != nil && *s.Smb1 {
		r.Add(core.Finding{ID: "smb1", Severity: core.High, Title: "SMB1 file sharing protocol is enabled",
			Detail: "SMB1 is the 30-year-old protocol exploited by WannaCry. Nothing modern needs it.",
			Fixes: []core.Fix{{ID: "disable", Title: "Remove SMB1", Risk: core.Moderate, Admin: true,
				Action: core.Action{Kind: core.ActPS, Script: "Disable-WindowsOptionalFeature -Online -FeatureName SMB1Protocol -NoRestart"}}}})
	}
	t.Rows = append(t.Rows, []string{"Storage Sense (auto clean-up)", onOff(s.StorageSense == 1)})
	if s.StorageSense != 1 {
		key := `HKCU:\Software\Microsoft\Windows\CurrentVersion\StorageSense\Parameters\StoragePolicy`
		r.Add(core.Finding{ID: "storage-sense", Severity: core.Low, Title: "Storage Sense is off",
			Detail: "Windows' built-in janitor: empties temp files and old Recycle Bin items automatically so the disk never silently fills up.",
			Fixes: []core.Fix{{ID: "enable", Title: "Turn on Storage Sense", Risk: core.Safe, Reversible: true,
				Action: core.Action{Kind: core.ActPS, Script: regSetScript(key, "01", "DWord", "1")},
				Undo:   &core.Action{Kind: core.ActPS, Script: regSetScript(key, "01", "DWord", "0")}}}})
	}
	switch s.BitLocker {
	case 1:
		t.Rows = append(t.Rows, []string{"Drive encryption (BitLocker)", "ON"})
		r.Add(core.Finding{ID: "bitlocker-key", Severity: core.Info, Title: "Your system drive is encrypted — do you have the recovery key?",
			Detail: "After a BIOS update, motherboard swap or TPM reset Windows asks for a 48-digit key. Without it the data is gone forever. It is usually saved at https://account.microsoft.com/devices/recoverykey — check now.",
			Fixes: []core.Fix{{ID: "show", Title: "Show the recovery key so you can save it", Risk: core.Safe, Admin: true,
				Action: core.Action{Kind: core.ActExec, Cmd: []string{"manage-bde", "-protectors", "-get", sys.Known().SystemDrive}}}}})
	case 0:
		t.Rows = append(t.Rows, []string{"Drive encryption (BitLocker)", "off"})
	}
	if s.Hags == 1 {
		t.Rows = append(t.Rows, []string{"Hardware-accelerated GPU scheduling", "off"})
	} else if s.Hags == 2 {
		t.Rows = append(t.Rows, []string{"Hardware-accelerated GPU scheduling", "on"})
	}
	god := `{user}\Desktop\GodMode.{ED7BA470-8E54-465E-825C-99712043E01C}`
	if !sys.Exists(sys.ExpandKnown(god)) {
		r.Add(core.Finding{ID: "god-mode", Severity: core.Info, Title: "Secret: 'God Mode' — every Windows setting in one folder",
			Detail: "A special folder name unlocks a list of 200+ control panel tasks in one place.",
			Fixes: []core.Fix{{ID: "create", Title: "Create the God Mode folder on your Desktop", Risk: core.Safe, Reversible: true,
				Action: core.Action{Kind: core.ActPS, Script: "New-Item -ItemType Directory -Force -Path \"$([Environment]::GetFolderPath('Desktop'))\\GodMode.{ED7BA470-8E54-465E-825C-99712043E01C}\" | Out-Null"},
				Undo:   &core.Action{Kind: core.ActPS, Script: "Remove-Item -LiteralPath \"$([Environment]::GetFolderPath('Desktop'))\\GodMode.{ED7BA470-8E54-465E-825C-99712043E01C}\" -Force"}}}})
	}
	r.Tables = append(r.Tables, t)
	r.Summary = fmt.Sprintf("%s %s on %s.", s.Edition, s.Display, s.Model)
	adminNote(r)
	return r, nil
}

func runSysinfo(c *core.Ctx) (*core.Result, error) {
	r := &core.Result{Title: "System snapshot"}
	t := core.Table{Headers: []string{"Item", "Value"}}
	data := map[string]any{"admin": sys.IsAdmin(), "arch": runtime.GOARCH}
	if h, err := host.InfoWithContext(c); err == nil {
		t.Rows = append(t.Rows, []string{"OS", fmt.Sprintf("%s %s (%s)", h.Platform, h.PlatformVersion, h.KernelVersion)}, []string{"Host", h.Hostname},
			[]string{"Uptime", sys.Age(time.Duration(h.Uptime) * time.Second)})
		data["os"] = h
	}
	if ci, err := cpu.InfoWithContext(c); err == nil && len(ci) > 0 {
		n, _ := cpu.CountsWithContext(c, true)
		t.Rows = append(t.Rows, []string{"CPU", fmt.Sprintf("%s (%d threads)", strings.TrimSpace(ci[0].ModelName), n)})
		data["cpu"] = ci[0].ModelName
	}
	if vm, err := mem.VirtualMemoryWithContext(c); err == nil {
		t.Rows = append(t.Rows, []string{"RAM", fmt.Sprintf("%s (%.0f%% used)", sys.HumanBytes(int64(vm.Total)), vm.UsedPercent)})
		data["ram_bytes"] = vm.Total
	}
	if parts, err := disk.PartitionsWithContext(c, false); err == nil {
		for _, p := range parts {
			if u, err := disk.UsageWithContext(c, p.Mountpoint); err == nil && u.Total > 0 && (sys.IsWindows || p.Mountpoint == "/") {
				t.Rows = append(t.Rows, []string{"Drive " + p.Mountpoint, fmt.Sprintf("%s free of %s", sys.HumanBytes(int64(u.Free)), sys.HumanBytes(int64(u.Total)))})
			}
		}
	}
	t.Rows = append(t.Rows, []string{"Running as admin", fmt.Sprint(sys.IsAdmin())})
	r.Tables = append(r.Tables, t)
	r.Data = data
	r.Summary = "Snapshot ready."
	return r, nil
}
