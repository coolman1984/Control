package tools

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/coolman1984/performance/internal/kb"
)

// TestPowerShellParses feeds every embedded script to the real PowerShell
// parser. It runs only when WINSIGHT_PWSH points to pwsh/powershell.
func TestPowerShellParses(t *testing.T) {
	pwsh := os.Getenv("WINSIGHT_PWSH")
	if pwsh == "" {
		t.Skip("set WINSIGHT_PWSH to a PowerShell binary to parse-check scripts")
	}
	scripts := map[string]string{
		"startup": startupScript, "tasks": tasksScript, "memory": memScript, "power": powerScript,
		"hogs": strings.Replace(hogsScript, "DEEP", "$true", 1), "health": healthScript,
		"events": strings.Replace(eventsScript, "DAYS", "7", 1), "crashes": crashScript, "drivers": driversScript,
		"updates": strings.Replace(updatesScript, "CHECK", "$false", 1), "integrity": integrityScript, "net": netScript,
		"boot": bootScript, "missing": strings.Replace(missingScript, "NAMES", "@('Winmgmt','EventLog')", 1),
		"shortcuts": shortcutsScript, "programs": programsScript, "restorepoints": restorePointsScript, "secrets": secretsScript,
		"resetWU": resetWU, "recycle": recycleScript([]string{`C:\a b\it's.txt`}),
		"regset": regSetScript(`HKCU:\X`, "N", "DWord", "1"), "regstr": regSetScript(`HKCU:\X`, "N", "String", "it's"),
		"regbin":  regSetScript(`HKCU:\X`, "N", "Binary", "([byte[]](3,0,0,0,0,0,0,0,0,0,0,0))"),
		"regdel":  regRestoreScript(`HKCU:\X`, "N", "DWord", "", false),
		"service": serviceStartScript("wuauserv", 3, "start"),
	}
	for _, tw := range kb.Tweaks {
		scripts["tweak-"+tw.ID] = regSetScript(tw.Key, tw.Name, tw.Type, tw.Want)
	}
	dir := t.TempDir()
	for name, s := range scripts {
		wrapped := "$__r = & {\n" + s + "\n}\nConvertTo-Json -InputObject $__r -Depth 8 -Compress"
		f := filepath.Join(dir, name+".ps1")
		if err := os.WriteFile(f, []byte(wrapped), 0o644); err != nil {
			t.Fatal(err)
		}
		check := `$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile('` + f + `',[ref]$null,[ref]$e); if($e){ $e | % { $_.Extent.StartLineNumber.ToString() + ': ' + $_.Message }; exit 1 }`
		out, err := exec.Command(pwsh, "-NoProfile", "-NonInteractive", "-Command", check).CombinedOutput()
		if err != nil {
			t.Errorf("%s: %s", name, out)
		}
	}
}
