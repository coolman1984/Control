package kb

import "fmt"

// EventInfo explains a Windows event log entry in plain words.
type EventInfo struct {
	Meaning string
	Action  string
	Noise   bool // common and usually harmless
}

// Events is keyed by "Provider/ID" with a fallback of "*/ID".
var Events = map[string]EventInfo{
	"Microsoft-Windows-Kernel-Power/41": {Meaning: "The PC rebooted without shutting down cleanly (power loss, freeze, hard reset or BSOD).",
		Action: "If it repeats: check `crashes` for BSODs, test PSU/battery, update chipset & GPU drivers, disable overclocking."},
	"EventLog/6008": {Meaning: "Previous shutdown was unexpected.", Action: "Correlate with Kernel-Power 41 and `crashes`."},
	"Microsoft-Windows-WER-SystemErrorReporting/1001": {Meaning: "The PC recovered from a blue screen (bugcheck).", Action: "Run `crashes` to decode the stop code and culprit driver."},
	"disk/7":                                              {Meaning: "The disk reported a bad block.", Action: "Back up now. Run `chkdsk C: /r` (reboot) and check `health` for SMART status. Plan to replace the disk."},
	"disk/51":                                             {Meaning: "Error during a paging operation on disk.", Action: "Check cables (desktop) and disk health; back up important data."},
	"disk/153":                                            {Meaning: "Disk I/O was retried (slow or failing storage/controller).", Action: "Update storage controller driver/firmware; check SMART; causes freezes."},
	"storahci/129":                                        {Meaning: "Storage controller reset (SATA).", Action: "Update chipset/AHCI driver, check SATA cable, disable aggressive link power management."},
	"stornvme/129":                                        {Meaning: "NVMe controller reset.", Action: "Update SSD firmware and chipset driver; check `health` for disk wear."},
	"Ntfs/55":                                             {Meaning: "File system corruption detected.", Action: "Run `chkdsk C: /f` (schedules at reboot)."},
	"Ntfs/98":                                             {Meaning: "Volume needs a consistency check.", Action: "Run `chkdsk <drive>: /f`."},
	"Microsoft-Windows-Ntfs/98":                           {Meaning: "Volume needs a consistency check.", Action: "Run `chkdsk <drive>: /f`."},
	"Service Control Manager/7000":                        {Meaning: "A service failed to start.", Action: "See which service; if it belongs to uninstalled software, remove it or set it to Disabled."},
	"Service Control Manager/7009":                        {Meaning: "A service took too long to start (timeout).", Action: "Often a slow disk or a broken third-party service; consider setting it to Automatic (Delayed Start)."},
	"Service Control Manager/7011":                        {Meaning: "A service stopped responding (timeout).", Action: "Usually a slow disk or overloaded system at that moment."},
	"Service Control Manager/7023":                        {Meaning: "A service stopped with an error.", Action: "Check the named service; reinstall/update its app."},
	"Service Control Manager/7031":                        {Meaning: "A service crashed and was restarted.", Action: "Repeated crashes of a Windows service → run `sfc /scannow`."},
	"Service Control Manager/7034":                        {Meaning: "A service stopped unexpectedly.", Action: "Update or remove the app owning the service."},
	"Microsoft-Windows-DistributedCOM/10016":              {Meaning: "A COM permission warning.", Action: "Harmless noise on almost every PC; ignore.", Noise: true},
	"Microsoft-Windows-DistributedCOM/10010":              {Meaning: "A COM server did not register in time.", Action: "Usually harmless.", Noise: true},
	"Application Error/1000":                              {Meaning: "An application crashed.", Action: "Update or reinstall the named app; if it's a Windows component run `sfc /scannow`."},
	"Application Hang/1002":                               {Meaning: "An application froze and was closed.", Action: "Update the app; look for disk or antivirus slowness."},
	".NET Runtime/1026":                                   {Meaning: "A .NET app crashed with an unhandled exception.", Action: "Update or repair the app / .NET runtime."},
	"Microsoft-Windows-Kernel-PnP/219":                    {Meaning: "A driver failed to load for a device.", Action: "Run `drivers` to find the device and update/reinstall its driver.", Noise: true},
	"Display/4101":                                        {Meaning: "The graphics driver stopped responding and recovered (TDR).", Action: "Clean-install the latest GPU driver; check GPU temperature; undo overclocks."},
	"nvlddmkm/13":                                         {Meaning: "NVIDIA driver error.", Action: "Clean-install the NVIDIA driver (DDU), check temps/power."},
	"nvlddmkm/14":                                         {Meaning: "NVIDIA driver error.", Action: "Clean-install the NVIDIA driver (DDU), check temps/power."},
	"Microsoft-Windows-WHEA-Logger/17":                    {Meaning: "Corrected hardware error (usually PCIe).", Action: "Often harmless; if frequent, update BIOS/chipset or set PCIe ASPM off.", Noise: true},
	"Microsoft-Windows-WHEA-Logger/18":                    {Meaning: "Fatal hardware error (CPU/memory/bus).", Action: "Remove overclocks/XMP, update BIOS, test RAM (mdsched) and CPU temps."},
	"Microsoft-Windows-WHEA-Logger/19":                    {Meaning: "Corrected hardware error (CPU cache/memory).", Action: "Check overclocks and temperatures."},
	"Microsoft-Windows-WHEA-Logger/1":                     {Meaning: "Hardware error reported by the platform.", Action: "Update BIOS and check hardware."},
	"Microsoft-Windows-DNS-Client/1014":                   {Meaning: "A DNS lookup timed out.", Action: "Harmless if occasional; if frequent, run `net` and consider a faster DNS.", Noise: true},
	"Schannel/36887":                                      {Meaning: "A TLS alert from a remote server.", Action: "Harmless noise.", Noise: true},
	"Schannel/36874":                                      {Meaning: "TLS protocol mismatch with a remote client.", Action: "Harmless noise.", Noise: true},
	"Microsoft-Windows-WindowsUpdateClient/20":            {Meaning: "A Windows Update failed to install.", Action: "Run `updates`; try the Windows Update troubleshooter or reset components."},
	"Microsoft-Windows-Time-Service/134":                  {Meaning: "Time sync could not reach the time server.", Action: "Harmless if the clock is correct.", Noise: true},
	"Microsoft-Windows-Winlogon/6005":                     {Meaning: "Winlogon notification took long.", Action: "Usually a slow logon script or profile.", Noise: true},
	"volmgr/46":                                           {Meaning: "Crash dump initialisation failed.", Action: "Ensure a page file exists on C:.", Noise: true},
	"Microsoft-Windows-Kernel-General/16":                 {Meaning: "Access history in a registry hive was cleared.", Action: "Harmless.", Noise: true},
	"Microsoft-Windows-Kernel-Processor-Power/37":         {Meaning: "CPU speed was limited by firmware (thermal/power).", Action: "Check cooling and power plan; laptops: plug in AC."},
	"Microsoft-Windows-Resource-Exhaustion-Detector/2004": {Meaning: "Windows ran out of virtual memory.", Action: "Run `memory`; close the leaking app or enlarge the page file."},
	"Microsoft-Windows-Diagnostics-Performance/100":       {Meaning: "Boot performance record.", Action: "See `boot`.", Noise: true},
}

// ExplainEvent looks up an event by provider and id.
func ExplainEvent(provider string, id int) (EventInfo, bool) {
	if e, ok := Events[fmt.Sprintf("%s/%d", provider, id)]; ok {
		return e, true
	}
	e, ok := Events[fmt.Sprintf("*/%d", id)]
	return e, ok
}

// BugChecks decodes blue-screen stop codes.
var BugChecks = map[uint32][2]string{
	0x0A:       {"IRQL_NOT_LESS_OR_EQUAL", "A driver touched memory it shouldn't. Update drivers (network, GPU, chipset); test RAM."},
	0x1A:       {"MEMORY_MANAGEMENT", "Often faulty RAM or unstable XMP/overclock. Run Windows Memory Diagnostic (mdsched)."},
	0x1E:       {"KMODE_EXCEPTION_NOT_HANDLED", "A kernel driver crashed. Update the driver named in the dump."},
	0x3B:       {"SYSTEM_SERVICE_EXCEPTION", "A driver or system file failed. Update GPU/antivirus drivers, run `sfc /scannow`."},
	0x3D:       {"INTERRUPT_EXCEPTION_NOT_HANDLED", "Driver/hardware interrupt problem. Update chipset drivers."},
	0x50:       {"PAGE_FAULT_IN_NONPAGED_AREA", "Bad driver, antivirus or RAM. Update drivers, test RAM."},
	0x74:       {"BAD_SYSTEM_CONFIG_INFO", "Registry/boot configuration damage. Use System Restore or Startup Repair."},
	0x7A:       {"KERNEL_DATA_INPAGE_ERROR", "Could not read memory back from disk: failing disk/cable or RAM. Check `health`."},
	0x7B:       {"INACCESSIBLE_BOOT_DEVICE", "Storage controller mode or driver changed. Check BIOS SATA mode (AHCI/RAID)."},
	0x7E:       {"SYSTEM_THREAD_EXCEPTION_NOT_HANDLED", "A system thread crashed, usually a driver. Update the named driver."},
	0x7F:       {"UNEXPECTED_KERNEL_MODE_TRAP", "Hardware fault or overheating/overclock."},
	0x9C:       {"MACHINE_CHECK_EXCEPTION", "CPU-reported hardware error. Check temps, BIOS, overclocks."},
	0x9F:       {"DRIVER_POWER_STATE_FAILURE", "A driver mishandled sleep/wake. Update network/USB/GPU drivers; disable Fast Startup."},
	0xC2:       {"BAD_POOL_CALLER", "Driver memory misuse. Update/uninstall recent drivers or antivirus."},
	0xC5:       {"DRIVER_CORRUPTED_EXPOOL", "Driver corrupted memory. Update drivers."},
	0xD1:       {"DRIVER_IRQL_NOT_LESS_OR_EQUAL", "Driver bug (very often network or Wi-Fi). Update that driver."},
	0xEF:       {"CRITICAL_PROCESS_DIED", "A core Windows process died. Run `sfc /scannow` and `DISM /Online /Cleanup-Image /RestoreHealth`; check disk."},
	0xED:       {"UNMOUNTABLE_BOOT_VOLUME", "Boot volume unreadable. Run `chkdsk /f /r` from recovery."},
	0xF4:       {"CRITICAL_OBJECT_TERMINATION", "Critical process ended; often failing storage."},
	0x101:      {"CLOCK_WATCHDOG_TIMEOUT", "A CPU core stopped responding. Remove overclock/undervolt, update BIOS."},
	0x116:      {"VIDEO_TDR_FAILURE", "GPU driver hung. Clean-install GPU driver, check temps/power."},
	0x117:      {"VIDEO_TDR_TIMEOUT_DETECTED", "GPU driver hung. Clean-install GPU driver."},
	0x119:      {"VIDEO_SCHEDULER_INTERNAL_ERROR", "GPU driver problem. Update GPU driver."},
	0x124:      {"WHEA_UNCORRECTABLE_ERROR", "Hardware error (CPU/RAM/PSU/overclock). Remove overclocks, update BIOS, check temps."},
	0x133:      {"DPC_WATCHDOG_VIOLATION", "A driver took too long (often SSD/storage or old SATA driver). Update SSD firmware and storage driver."},
	0x139:      {"KERNEL_SECURITY_CHECK_FAILURE", "Data corruption from a driver or RAM. Update drivers, test RAM, run sfc."},
	0x13A:      {"KERNEL_MODE_HEAP_CORRUPTION", "Driver corrupted memory. Update GPU/audio drivers."},
	0x154:      {"UNEXPECTED_STORE_EXCEPTION", "Memory compression store failed: disk or RAM. Check `health`."},
	0x19:       {"BAD_POOL_HEADER", "Driver memory corruption, often antivirus or VPN drivers."},
	0x3F:       {"NO_MORE_SYSTEM_PTES", "Driver leaked system resources."},
	0x1000007E: {"SYSTEM_THREAD_EXCEPTION_NOT_HANDLED_M", "A system thread crashed, usually a driver."},
	0x1000008E: {"KERNEL_MODE_EXCEPTION_NOT_HANDLED_M", "Driver or RAM fault."},
	0xC000021A: {"STATUS_SYSTEM_PROCESS_TERMINATED", "Winlogon/CSRSS died. Run sfc/DISM from recovery or use System Restore."},
}

// ExplainBugCheck returns name and advice for a stop code.
func ExplainBugCheck(code uint32) (string, string) {
	if v, ok := BugChecks[code]; ok {
		return v[0], v[1]
	}
	return fmt.Sprintf("0x%08X", code), "Look up this stop code; analyse the dump with WinDbg (`!analyze -v`)."
}
