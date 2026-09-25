// Package tools contains every WinSight diagnostic. Each file registers a
// family of small, specialised tools with core.Register in init().
package tools

import (
	"errors"
	"fmt"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/coolman1984/performance/internal/core"
	"github.com/coolman1984/performance/internal/kb"
	"github.com/coolman1984/performance/internal/sys"
)

// windowsOnly produces a friendly result when a probe needs Windows.
func windowsOnly(r *core.Result, err error) bool {
	if errors.Is(err, sys.ErrNotWindows) {
		r.Summary = "This check only runs on Windows."
		return true
	}
	if err != nil {
		r.Errf("%v", err)
	}
	return false
}

func adminNote(r *core.Result) {
	if !sys.IsAdmin() {
		r.Notes = append(r.Notes, "Some details need administrator rights. Re-run WinSight as administrator for the full picture.")
	}
}

var slugRe = regexp.MustCompile(`[^a-z0-9]+`)

// slug makes a stable id fragment from free text.
func slug(s string) string {
	s = strings.Trim(slugRe.ReplaceAllString(strings.ToLower(s), "-"), "-")
	if len(s) > 40 {
		s = s[:40]
	}
	if s == "" {
		s = "x"
	}
	return s
}

// exePath extracts the program path from a command line.
func exePath(cmdline string) string {
	c := strings.TrimSpace(sys.Expand(cmdline))
	if c == "" {
		return ""
	}
	if c[0] == '"' {
		if end := strings.IndexByte(c[1:], '"'); end >= 0 {
			return c[1 : end+1]
		}
		return strings.Trim(c, `"`)
	}
	lc := strings.ToLower(c)
	for _, ext := range []string{".exe", ".cmd", ".bat", ".com", ".lnk", ".vbs", ".ps1", ".dll"} {
		if i := strings.Index(lc, ext); i >= 0 {
			return c[:i+len(ext)]
		}
	}
	if sp := strings.IndexByte(c, ' '); sp > 0 {
		return c[:sp]
	}
	return c
}

// targetMissing reports whether a command's program is definitely absent.
// Bare program names (resolved through PATH) are looked up too.
func targetMissing(cmdline string) (string, bool) {
	p := exePath(cmdline)
	if p == "" {
		return "", false
	}
	if !strings.ContainsAny(p, `\/`) {
		return p, false // bare names resolve through PATH; never report as missing
	}
	if strings.EqualFold(filepath.Base(p), "rundll32.exe") {
		return p, false
	}
	return p, !sys.Exists(p)
}

// regSetScript writes a registry value, creating the key when needed.
func regSetScript(key, name, typ, value string) string {
	v := value // numeric literal
	if typ == "String" || typ == "ExpandString" {
		v = sys.PSQuote(value)
	}
	return fmt.Sprintf("if(-not (Test-Path %[1]s)){New-Item -Path %[1]s -Force | Out-Null}\nSet-ItemProperty -Path %[1]s -Name %[2]s -Value %[3]s -Type %[4]s -Force",
		sys.PSQuote(key), sys.PSQuote(name), v, typ)
}

// regRestoreScript puts a value back the way it was (or removes it).
func regRestoreScript(key, name, typ, prev string, existed bool) string {
	if !existed {
		return fmt.Sprintf("Remove-ItemProperty -Path %s -Name %s -Force", sys.PSQuote(key), sys.PSQuote(name))
	}
	return regSetScript(key, name, typ, prev)
}

// serviceStartScript sets a service's start type via the registry (works for
// services whose ACLs block Set-Service) and optionally starts/stops it.
func serviceStartScript(name string, start int, run string) string {
	s := fmt.Sprintf("Set-ItemProperty -Path %s -Name Start -Value %d -Type DWord -Force",
		sys.PSQuote(`HKLM:\SYSTEM\CurrentControlSet\Services\`+name), start)
	switch run {
	case "start":
		s += "\nStart-Service -Name " + sys.PSQuote(name)
	case "stop":
		s += "\nStop-Service -Name " + sys.PSQuote(name) + " -Force"
	}
	return s
}

// recycleScript sends files/folders to the Recycle Bin (recoverable).
func recycleScript(paths []string) string {
	return "Add-Type -AssemblyName Microsoft.VisualBasic\nforeach($p in " + sys.PSArray(paths) + "){\n" +
		"  if(Test-Path -LiteralPath $p -PathType Container){[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($p,'OnlyErrorDialogs','SendToRecycleBin')}\n" +
		"  elseif(Test-Path -LiteralPath $p){[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($p,'OnlyErrorDialogs','SendToRecycleBin')}\n}"
}

// spotSize measures a junk spot.
func spotSize(c *core.Ctx, s kb.JunkSpot) (int64, int64, []string) {
	var total, files int64
	var found []string
	cutoff := time.Now().Add(time.Duration(s.OlderThanDays) * -24 * time.Hour)
	for _, pat := range s.Paths {
		for _, p := range sys.Glob(sys.ExpandKnown(pat)) {
			found = append(found, p)
			var b, n int64
			if s.OlderThanDays > 0 {
				b, n = sys.SizeOlderThan(c, p, cutoff)
			} else {
				b, n = sys.DirSize(c, p)
			}
			total += b
			files += n
		}
	}
	return total, files, found
}

// junkFindings scans a set of spots in parallel and turns them into findings.
func junkFindings(c *core.Ctx, r *core.Result, spots []kb.JunkSpot, minBytes int64) {
	type res struct {
		s     kb.JunkSpot
		b, n  int64
		found []string
	}
	out := make([]res, len(spots))
	var wg sync.WaitGroup
	sem := make(chan struct{}, 6)
	for i, s := range spots {
		wg.Add(1)
		go func(i int, s kb.JunkSpot) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()
			c.Progress("measuring %s", s.Title)
			b, n, found := spotSize(c, s)
			out[i] = res{s, b, n, found}
		}(i, s)
	}
	wg.Wait()
	sort.Slice(out, func(i, j int) bool { return out[i].b > out[j].b })
	t := core.Table{Title: "Where the space is", Headers: []string{"Location", "Size", "Files", "Risk"}}
	for _, o := range out {
		if len(o.found) == 0 || o.b < minBytes {
			continue
		}
		s := o.s
		sev := core.Info
		switch {
		case o.b > 5<<30:
			sev = core.High
		case o.b > 1<<30:
			sev = core.Medium
		case o.b > 200<<20:
			sev = core.Low
		}
		f := core.Finding{ID: s.ID, Severity: sev, Title: s.Title, Detail: s.Why, Bytes: o.b, Evidence: o.found,
			Data: map[string]any{"files": o.n, "group": s.Group}}
		fx := core.Fix{ID: "clean", Title: "Clean " + s.Title, Risk: s.Risk, Admin: s.Admin,
			Action: core.Action{Kind: core.ActClean, Paths: s.Paths, OlderThanDays: s.OlderThanDays},
			Verify: "winsight " + r.Tool}
		if len(s.Cmd) > 0 {
			if _, err := exec.LookPath(s.Cmd[0]); err == nil {
				fx.Action = core.Action{Kind: core.ActExec, Cmd: s.Cmd}
				fx.Title = "Run " + strings.Join(s.Cmd, " ")
			}
		}
		f.Fixes = append(f.Fixes, fx)
		r.Add(f)
		t.Rows = append(t.Rows, []string{s.Title, sys.HumanBytes(o.b), fmt.Sprint(o.n), string(s.Risk)})
	}
	if len(t.Rows) > 0 {
		r.Tables = append(r.Tables, t)
	}
}
