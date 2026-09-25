package tools

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/shirou/gopsutil/v4/disk"

	"github.com/coolman1984/performance/internal/core"
	"github.com/coolman1984/performance/internal/kb"
	"github.com/coolman1984/performance/internal/sys"
)

func init() {
	core.Register(&core.Tool{Name: "drives", Category: "space", InBrief: true,
		Short: "How full every drive is", Aliases: []string{"disks", "df"},
		Keywords: []string{"drive", "disk", "full", "space", "storage", "free", "مساحة", "الهارد", "هارد", "ديسك", "مليان"},
		Run:      runDrives})
	core.Register(&core.Tool{Name: "junk", Category: "space", InBrief: true, Slow: true,
		Short:    "Temp files, caches, update leftovers, crash dumps, browser caches, recycle bin",
		Long:     "Measures every place Windows and common apps pile up regenerable data and attaches a one-command clean for each.",
		Keywords: []string{"junk", "temp", "cache", "clean", "cleanup", "free up", "free space", "free", "space", "وفر مساحة", "مساحة", "trash", "تنظيف", "مؤقتة", "كاش", "زبالة", "وفر", "تفضية", "فضي"},
		Run:      runJunk})
	core.Register(&core.Tool{Name: "devcache", Category: "space", InBrief: true, Slow: true,
		Short:    "Developer caches: npm, pip, NuGet, Gradle, Maven, Cargo, Go, Docker, models",
		Keywords: []string{"npm", "pip", "nuget", "gradle", "docker", "developer", "dev", "cache", "مطور", "برمجة"},
		Run:      runDevCache})
	core.Register(&core.Tool{Name: "bigfiles", Category: "space", Slow: true,
		Short: "The biggest files on a drive or folder",
		Params: []core.Param{{Name: "path", Desc: "folder or drive to scan", Default: "your user folder"},
			{Name: "top", Desc: "how many files", Default: "25", Type: "int"}, {Name: "min", Desc: "minimum size in MB", Default: "100", Type: "int"}},
		Keywords: []string{"big", "large", "biggest", "huge", "files", "كبيرة", "اكبر", "ملفات"},
		Run:      runBigFiles})
	core.Register(&core.Tool{Name: "topdirs", Category: "space", Slow: true,
		Short:    "Which folders eat the space (two levels deep)",
		Params:   []core.Param{{Name: "path", Desc: "folder or drive", Default: "system drive"}},
		Aliases:  []string{"tree", "du"},
		Keywords: []string{"folder", "folders", "which", "eating", "فولدر", "مجلدات", "واكلة"},
		Run:      runTopDirs})
	core.Register(&core.Tool{Name: "dupes", Category: "space", Slow: true,
		Short: "Duplicate files (same content) and the space they waste",
		Params: []core.Param{{Name: "path", Desc: "folder to scan", Default: "your user folder"},
			{Name: "min", Desc: "minimum size in MB", Default: "1", Type: "int"}},
		Aliases:  []string{"duplicates"},
		Keywords: []string{"duplicate", "duplicates", "copies", "same", "مكرر", "مكررة", "نسخ"},
		Run:      runDupes})
	core.Register(&core.Tool{Name: "buildjunk", Category: "space", InBrief: true, Slow: true,
		Short: "Stale node_modules, venv, target, bin/obj folders in old projects",
		Params: []core.Param{{Name: "path", Desc: "where your projects live", Default: "your user folder"},
			{Name: "days", Desc: "untouched for at least N days", Default: "30", Type: "int"}},
		Keywords: []string{"node_modules", "venv", "build", "projects", "مشاريع"},
		Run:      runBuildJunk})
	core.Register(&core.Tool{Name: "hiddenhogs", Category: "space", InBrief: true, Admin: true,
		Short:    "Space Windows hides: hibernation, page file, Windows.old, WinSxS, restore points, reserved storage, WSL disks",
		Params:   []core.Param{{Name: "deep", Desc: "also analyse the component store (slow, admin)", Type: "bool"}},
		Keywords: []string{"hidden", "hibernation", "hiberfil", "pagefile", "winsxs", "windows.old", "reserved", "مخفية", "سر", "خفي"},
		Run:      runHiddenHogs})
	core.Register(&core.Tool{Name: "downloads", Category: "space", InBrief: true,
		Short:    "Old installers, archives and disk images forgotten in Downloads",
		Params:   []core.Param{{Name: "days", Desc: "older than N days", Default: "30", Type: "int"}},
		Keywords: []string{"downloads", "installers", "setup", "iso", "zip", "التنزيلات", "داونلود"},
		Run:      runDownloads})
}

func runDrives(c *core.Ctx) (*core.Result, error) {
	r := &core.Result{Title: "Drives"}
	parts, err := disk.PartitionsWithContext(c, false)
	if err != nil {
		return r, err
	}
	t := core.Table{Headers: []string{"Drive", "Type", "Size", "Used", "Free", "Use%"}}
	sysDrive := strings.ToUpper(sys.Known().SystemDrive)
	var lowest float64 = 100
	for _, p := range parts {
		u, err := disk.UsageWithContext(c, p.Mountpoint)
		if err != nil || u.Total == 0 {
			continue
		}
		if !sys.IsWindows && (p.Fstype == "squashfs" || p.Fstype == "tmpfs" || p.Fstype == "overlay" || u.Total < 1<<30 ||
			strings.HasPrefix(p.Mountpoint, "/proc") || strings.HasPrefix(p.Mountpoint, "/sys") || strings.HasPrefix(p.Mountpoint, "/dev")) {
			continue
		}
		freePct := float64(u.Free) * 100 / float64(u.Total)
		if freePct < lowest {
			lowest = freePct
		}
		t.Rows = append(t.Rows, []string{p.Mountpoint, p.Fstype, sys.HumanBytes(int64(u.Total)), sys.HumanBytes(int64(u.Used)),
			sys.HumanBytes(int64(u.Free)), fmt.Sprintf("%.0f%%", u.UsedPercent)})
		isSys := strings.EqualFold(strings.TrimRight(p.Mountpoint, `\`), sysDrive)
		id := slug("drive-" + p.Mountpoint)
		switch {
		case freePct < 5 || (isSys && u.Free < 5<<30):
			r.Add(core.Finding{ID: id, Severity: core.Critical, Title: fmt.Sprintf("%s is almost full (%s free)", p.Mountpoint, sys.HumanBytes(int64(u.Free))),
				Detail: "Below ~10% free, Windows slows down, updates fail and apps crash. Run `junk`, `hiddenhogs` and `devcache` to reclaim space.",
				Data:   map[string]any{"free_bytes": u.Free, "total_bytes": u.Total}})
		case freePct < 12:
			r.Add(core.Finding{ID: id, Severity: core.High, Title: fmt.Sprintf("%s is getting full (%.0f%% free)", p.Mountpoint, freePct),
				Detail: "Keep at least 15% free on SSDs for speed and wear levelling.", Data: map[string]any{"free_bytes": u.Free, "total_bytes": u.Total}})
		case freePct < 20:
			r.Add(core.Finding{ID: id, Severity: core.Low, Title: fmt.Sprintf("%s has %.0f%% free", p.Mountpoint, freePct)})
		}
	}
	r.Tables = append(r.Tables, t)
	r.Summary = fmt.Sprintf("%d drive(s); tightest has %.0f%% free.", len(t.Rows), lowest)
	return r, nil
}

func runJunk(c *core.Ctx) (*core.Result, error) {
	r := &core.Result{Title: "Junk & caches"}
	var spots []kb.JunkSpot
	for _, s := range kb.JunkSpots {
		if s.Group != "dev" || strings.HasPrefix(s.ID, "vscode") {
			spots = append(spots, s)
		}
	}
	junkFindings(c, r, spots, 10<<20)
	recycleBinFinding(c, r)
	total := r.ReclaimableBytes()
	r.Summary = fmt.Sprintf("%s can be reclaimed from %d places.", sys.HumanBytes(total), len(r.Findings))
	if total > 0 {
		r.Notes = append(r.Notes, "`clean` applies every safe fix above in one go. Nothing personal (documents, logins, bookmarks) is touched.")
	}
	return r, nil
}

// recycleBinFinding measures the Recycle Bin on every fixed drive.
func recycleBinFinding(c *core.Ctx, r *core.Result) {
	if !sys.IsWindows {
		return
	}
	parts, _ := disk.PartitionsWithContext(c, false)
	var total int64
	var where []string
	for _, p := range parts {
		bin := filepath.Join(p.Mountpoint, "$Recycle.Bin")
		if b, _ := sys.DirSize(c, bin); b > 0 {
			total += b
			where = append(where, bin)
		}
	}
	if total < 50<<20 {
		return
	}
	r.Add(core.Finding{ID: "recycle-bin", Severity: core.Low, Title: "Recycle Bin", Bytes: total, Evidence: where,
		Detail: "Deleted files still take space until the bin is emptied. Run `recover` first if something might have been deleted by mistake.",
		Fixes: []core.Fix{{ID: "empty", Title: "Empty the Recycle Bin (permanent)", Risk: core.Moderate,
			Action: core.Action{Kind: core.ActPS, Script: "Clear-RecycleBin -Force"}}}})
}

func runDevCache(c *core.Ctx) (*core.Result, error) {
	r := &core.Result{Title: "Developer caches"}
	var spots []kb.JunkSpot
	for _, s := range kb.JunkSpots {
		if s.Group == "dev" {
			spots = append(spots, s)
		}
	}
	junkFindings(c, r, spots, 50<<20)
	r.Summary = fmt.Sprintf("%s in developer caches (%d found).", sys.HumanBytes(r.ReclaimableBytes()), len(r.Findings))
	return r, nil
}

func defaultRoot(c *core.Ctx, def string) string {
	p := c.Str("path", "")
	if p == "" && len(c.Pos) > 0 {
		p = c.Pos[0]
	}
	if p == "" {
		p = def
	}
	p = sys.ExpandKnown(p)
	if len(p) == 2 && p[1] == ':' {
		p += `\`
	}
	return p
}

var fileHints = map[string]string{
	".iso":  "Disk image — usually only needed once to install something.",
	".vhdx": "Virtual disk (WSL/Hyper-V/Docker). Compact it instead of deleting.",
	".vhd":  "Virtual disk.", ".vmdk": "VMware disk.", ".vdi": "VirtualBox disk.",
	".zip": "Archive — often already extracted.", ".rar": "Archive.", ".7z": "Archive.",
	".exe": "Installer — can usually be downloaded again.", ".msi": "Installer.",
	".mp4": "Video.", ".mkv": "Video.", ".mov": "Video.",
	".ost": "Outlook offline cache — rebuilt from the server if removed (close Outlook).",
	".pst": "Outlook data file — may be your ONLY copy of old mail. Keep.",
	".dmp": "Crash dump.", ".log": "Log file.", ".bak": "Backup copy.",
	".gguf": "Local AI model weights.", ".safetensors": "AI model weights.",
}

func runBigFiles(c *core.Ctx) (*core.Result, error) {
	root := defaultRoot(c, "{user}")
	top := c.Int("top", 25)
	minB := int64(c.Int("min", 100)) << 20
	r := &core.Result{Title: "Biggest files in " + root}
	c.Progress("scanning %s", root)
	tn := sys.NewTopN(top)
	st := sys.Walk(c, root, sys.WalkOptions{OnFile: func(p string, size int64, mod time.Time) {
		if size >= minB {
			tn.Offer(sys.SizedPath{Path: p, Bytes: size, Mod: mod})
		}
	}})
	items := tn.Sorted()
	t := core.Table{Headers: []string{"Size", "Modified", "File", "Hint"}}
	var total int64
	for _, it := range items {
		hint := fileHints[strings.ToLower(filepath.Ext(it.Path))]
		t.Rows = append(t.Rows, []string{sys.HumanBytes(it.Bytes), it.Mod.Format("2006-01-02"), it.Path, hint})
		total += it.Bytes
		if strings.EqualFold(filepath.Ext(it.Path), ".pst") {
			continue
		}
		r.Add(core.Finding{ID: "file-" + shortHash(it.Path), Severity: core.Info, Title: filepath.Base(it.Path), Bytes: 0,
			Detail: hint, Evidence: []string{it.Path}, Data: map[string]any{"bytes": it.Bytes, "modified": it.Mod},
			Fixes: []core.Fix{{ID: "recycle", Title: "Send to Recycle Bin", Risk: core.Risky, Reversible: true,
				Action: core.Action{Kind: core.ActPS, Script: recycleScript([]string{it.Path})}}}})
	}
	r.Tables = append(r.Tables, t)
	r.Data = items
	r.Summary = fmt.Sprintf("Top %d files ≥ %s hold %s (scanned %d files, %s).", len(items), sys.HumanBytes(minB), sys.HumanBytes(total), st.Files, sys.HumanBytes(st.Bytes))
	r.Notes = append(r.Notes, "These are personal files: WinSight never deletes them automatically. Each has a `recycle` fix you can apply after checking.")
	return r, nil
}

func shortHash(s string) string {
	h := sha256.Sum256([]byte(strings.ToLower(s)))
	return hex.EncodeToString(h[:])[:10]
}

var dirHints = map[string]string{
	"winsxs":                    "Windows component store — shrink only with DISM (`hiddenhogs`), never delete.",
	"installer":                 "Windows Installer cache — needed to repair/uninstall programs. Never delete by hand.",
	"softwaredistribution":      "Windows Update working folder; its Download subfolder is safe to clean (`junk`).",
	"appdata":                   "Per-user app data and caches — run `junk` and `devcache`.",
	"packages":                  "Store apps data (incl. WSL disks) — see `hiddenhogs`.",
	"node_modules":              "JavaScript dependencies — reinstallable (`buildjunk`).",
	"windows.old":               "Previous Windows install — removable after 10 days (`hiddenhogs`).",
	"system volume information": "Restore points and shadow copies (`hiddenhogs`).",
	"$recycle.bin":              "Recycle Bin.",
	"downloads":                 "Check `downloads` for old installers.",
	"docker":                    "Docker images/volumes — `docker system prune`.",
	".cache":                    "Tool caches (models, pip, etc.).",
}

func runTopDirs(c *core.Ctx) (*core.Result, error) {
	root := defaultRoot(c, sys.Known().SystemDrive+`\`)
	if !sys.IsWindows && c.Str("path", "") == "" && len(c.Pos) == 0 {
		root, _ = os.UserHomeDir()
	}
	r := &core.Result{Title: "Heaviest folders in " + root}
	c.Progress("measuring %s", root)
	level1 := sys.ChildSizes(c, root)
	var total int64
	for _, s := range level1 {
		total += s.Bytes
	}
	t := core.Table{Headers: []string{"Size", "Share", "Folder", "Hint"}}
	for i, s := range level1 {
		if i >= 12 {
			break
		}
		t.Rows = append(t.Rows, []string{sys.HumanBytes(s.Bytes), sys.Pct(float64(s.Bytes), float64(total)), s.Path, dirHints[strings.ToLower(filepath.Base(s.Path))]})
		if i < 4 && !strings.HasSuffix(s.Path, "<files>") && s.Bytes > 1<<30 {
			c.Progress("drilling into %s", s.Path)
			for j, sub := range sys.ChildSizes(c, s.Path) {
				if j >= 5 || sub.Bytes < 256<<20 {
					break
				}
				t.Rows = append(t.Rows, []string{"  " + sys.HumanBytes(sub.Bytes), sys.Pct(float64(sub.Bytes), float64(total)), "  └ " + sub.Path, dirHints[strings.ToLower(filepath.Base(sub.Path))]})
			}
		}
	}
	r.Tables = append(r.Tables, t)
	r.Data = level1
	r.Summary = fmt.Sprintf("%s measured under %s.", sys.HumanBytes(total), root)
	adminNote(r)
	return r, nil
}

func runDupes(c *core.Ctx) (*core.Result, error) {
	root := defaultRoot(c, "{user}")
	minB := int64(c.Int("min", 1)) << 20
	r := &core.Result{Title: "Duplicate files in " + root}
	c.Progress("indexing %s", root)
	var mu sync.Mutex
	bySize := map[int64][]string{}
	sys.Walk(c, root, sys.WalkOptions{
		SkipDir: func(_, name string) bool {
			n := strings.ToLower(name)
			return n == "node_modules" || n == ".git" || n == "appdata" || n == "$recycle.bin"
		},
		OnFile: func(p string, size int64, _ time.Time) {
			if size >= minB {
				mu.Lock()
				bySize[size] = append(bySize[size], p)
				mu.Unlock()
			}
		}})
	type group struct {
		size  int64
		paths []string
	}
	var groups []group
	checked := 0
	for size, paths := range bySize {
		if len(paths) < 2 || c.Err() != nil {
			continue
		}
		checked += len(paths)
		if checked%200 == 0 {
			c.Progress("hashing candidates (%d)", checked)
		}
		byHash := map[string][]string{}
		for _, p := range paths {
			if h, err := hashFile(p, size); err == nil {
				byHash[h] = append(byHash[h], p)
			}
		}
		for _, ps := range byHash {
			if len(ps) > 1 {
				sort.Slice(ps, func(i, j int) bool { return len(ps[i]) < len(ps[j]) })
				groups = append(groups, group{size, ps})
			}
		}
	}
	sort.Slice(groups, func(i, j int) bool {
		return groups[i].size*int64(len(groups[i].paths)-1) > groups[j].size*int64(len(groups[j].paths)-1)
	})
	var wasted int64
	t := core.Table{Headers: []string{"Wasted", "Copies", "Keep", "Duplicates"}}
	for i, g := range groups {
		w := g.size * int64(len(g.paths)-1)
		wasted += w
		if i >= 30 {
			continue
		}
		t.Rows = append(t.Rows, []string{sys.HumanBytes(w), fmt.Sprint(len(g.paths)), g.paths[0], strings.Join(g.paths[1:], " | ")})
		r.Add(core.Finding{ID: "dup-" + shortHash(g.paths[0]), Severity: core.Info, Title: filepath.Base(g.paths[0]) + fmt.Sprintf(" ×%d", len(g.paths)),
			Bytes: w, Evidence: g.paths, Detail: "Identical content (SHA-256). The copy with the shortest path is kept.",
			Fixes: []core.Fix{{ID: "recycle-extra", Title: "Send the extra copies to the Recycle Bin", Risk: core.Risky, Reversible: true,
				Action: core.Action{Kind: core.ActPS, Script: recycleScript(g.paths[1:])}}}})
	}
	r.Tables = append(r.Tables, t)
	r.Summary = fmt.Sprintf("%d duplicate groups waste %s.", len(groups), sys.HumanBytes(wasted))
	return r, nil
}

// hashFile hashes the first and last 64 KiB plus the size, then the whole
// file for files under 64 MiB; enough to separate real duplicates quickly.
func hashFile(p string, size int64) (string, error) {
	f, err := os.Open(p)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	fmt.Fprint(h, size)
	if size <= 64<<20 {
		if _, err := io.Copy(h, f); err != nil {
			return "", err
		}
	} else {
		buf := make([]byte, 64<<10)
		n, _ := io.ReadFull(f, buf)
		h.Write(buf[:n])
		if _, err := f.Seek(size/2, io.SeekStart); err == nil {
			n, _ = io.ReadFull(f, buf)
			h.Write(buf[:n])
		}
		if _, err := f.Seek(-int64(len(buf)), io.SeekEnd); err == nil {
			n, _ = io.ReadFull(f, buf)
			h.Write(buf[:n])
		}
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

// buildDirs maps a folder name to the marker that proves it is regenerable.
var buildDirs = map[string]string{
	"node_modules": "package.json", ".venv": "pyvenv.cfg*", "venv": "pyvenv.cfg*", "target": "Cargo.toml",
	".next": "package.json", ".nuxt": "package.json", ".parcel-cache": "package.json", ".gradle": "build.gradle*",
	"obj": "*.csproj", "bin": "*.csproj", ".terraform": "*.tf", "__pypackages__": "pyproject.toml", ".tox": "tox.ini",
}

func runBuildJunk(c *core.Ctx) (*core.Result, error) {
	root := defaultRoot(c, "{user}")
	days := c.Int("days", 30)
	cutoff := time.Now().Add(time.Duration(days) * -24 * time.Hour)
	r := &core.Result{Title: "Stale build folders in " + root}
	c.Progress("looking for projects in %s", root)
	var mu sync.Mutex
	var found []sys.SizedPath
	sys.Walk(c, root, sys.WalkOptions{SkipDir: func(p, name string) bool {
		n := strings.ToLower(name)
		if n == "appdata" || n == ".git" || n == "$recycle.bin" || n == "windows" || n == "program files" || n == "program files (x86)" {
			return true
		}
		marker, ok := buildDirs[n]
		if !ok {
			return false
		}
		parent := filepath.Dir(p)
		if n == "venv" || n == ".venv" {
			parent = p // pyvenv.cfg lives inside the venv
		}
		if m, _ := filepath.Glob(filepath.Join(parent, marker)); len(m) == 0 {
			return false
		}
		if fi, err := os.Stat(filepath.Dir(p)); err == nil && fi.ModTime().After(cutoff) {
			return true // active project: skip, do not descend
		}
		b, _ := sys.DirSize(c, p)
		mu.Lock()
		found = append(found, sys.SizedPath{Path: p, Bytes: b})
		mu.Unlock()
		return true
	}})
	sort.Slice(found, func(i, j int) bool { return found[i].Bytes > found[j].Bytes })
	t := core.Table{Headers: []string{"Size", "Folder"}}
	var total int64
	var paths []string
	for _, f := range found {
		if f.Bytes < 20<<20 {
			continue
		}
		total += f.Bytes
		paths = append(paths, f.Path)
		t.Rows = append(t.Rows, []string{sys.HumanBytes(f.Bytes), f.Path})
	}
	if len(paths) > 0 {
		r.Tables = append(r.Tables, t)
		r.Add(core.Finding{ID: "stale-build-dirs", Severity: sevForBytes(total), Title: fmt.Sprintf("%d build/dependency folders in projects untouched for %d+ days", len(paths), days),
			Bytes: total, Evidence: paths, Detail: "Regenerated by `npm install`, `pip install`, `cargo build`, `dotnet build` when you return to the project.",
			Fixes: []core.Fix{{ID: "delete", Title: "Delete these build folders", Risk: core.Moderate,
				Action: core.Action{Kind: core.ActDelete, Paths: paths}}}})
	}
	r.Summary = fmt.Sprintf("%s in %d stale build folders.", sys.HumanBytes(total), len(paths))
	return r, nil
}

func sevForBytes(b int64) core.Severity {
	switch {
	case b > 10<<30:
		return core.High
	case b > 2<<30:
		return core.Medium
	case b > 300<<20:
		return core.Low
	}
	return core.Info
}

func runDownloads(c *core.Ctx) (*core.Result, error) {
	days := c.Int("days", 30)
	dir := sys.Known().Downloads
	r := &core.Result{Title: "Old files in Downloads"}
	cutoff := time.Now().Add(time.Duration(days) * -24 * time.Hour)
	exts := map[string]bool{".exe": true, ".msi": true, ".msix": true, ".appx": true, ".zip": true, ".rar": true, ".7z": true, ".iso": true, ".img": true, ".tar": true, ".gz": true, ".cab": true}
	entries, err := os.ReadDir(dir)
	if err != nil {
		r.Summary = "No Downloads folder found."
		return r, nil
	}
	var paths []string
	var total int64
	t := core.Table{Headers: []string{"Size", "Age", "File"}}
	for _, e := range entries {
		fi, err := e.Info()
		if err != nil || e.IsDir() || !exts[strings.ToLower(filepath.Ext(e.Name()))] || !fi.ModTime().Before(cutoff) {
			continue
		}
		p := filepath.Join(dir, e.Name())
		paths = append(paths, p)
		total += fi.Size()
		t.Rows = append(t.Rows, []string{sys.HumanBytes(fi.Size()), sys.Age(time.Since(fi.ModTime())), e.Name()})
	}
	sort.Slice(t.Rows, func(i, j int) bool { return t.Rows[i][1] > t.Rows[j][1] })
	if len(paths) > 0 {
		r.Tables = append(r.Tables, t)
		r.Add(core.Finding{ID: "old-installers", Severity: sevForBytes(total), Title: fmt.Sprintf("%d old installers/archives/images in Downloads", len(paths)),
			Bytes: total, Evidence: paths, Detail: "Setup files and archives are rarely needed after installing/extracting and can be downloaded again.",
			Fixes: []core.Fix{{ID: "recycle", Title: "Send them to the Recycle Bin", Risk: core.Moderate, Reversible: true,
				Action: core.Action{Kind: core.ActPS, Script: recycleScript(paths)}}}})
	}
	r.Summary = fmt.Sprintf("%s in %d files older than %d days.", sys.HumanBytes(total), len(paths), days)
	return r, nil
}

type hogsPS struct {
	Hiberfil, Pagefile, Swapfile int64
	HibernateEnabled             bool
	Reserved                     string
	Shadow                       sys.List[struct {
		Volume    string
		Used, Max int64
		Allocated int64
	}]
	ShadowCopies int
	CompStore    string
}

const hogsScript = `
$sd = $env:SystemDrive + '\'
$o = [ordered]@{}
foreach($n in 'hiberfil.sys','pagefile.sys','swapfile.sys'){
  $f = Get-Item -LiteralPath ($sd + $n) -Force
  $k = $n.Split('.')[0]; $k = $k.Substring(0,1).ToUpper() + $k.Substring(1)
  $o[$k] = if($f){[int64]$f.Length}else{[int64]0}
}
$h = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Power' -Name HibernateEnabled
$o.HibernateEnabled = [bool]($h.HibernateEnabled -eq 1)
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($admin){
  try { $o.Reserved = [string](Get-WindowsReservedStorageState).ReservedStorageState } catch {}
  $vols = @{}; Get-CimInstance Win32_Volume | ForEach-Object { $vols[$_.DeviceID] = $_.DriveLetter }
  $o.Shadow = @(Get-CimInstance Win32_ShadowStorage | ForEach-Object {
    $id = [string]$_.Volume.DeviceID; $name = if($vols[$id]){$vols[$id]}else{$id}
    [pscustomobject]@{ Volume = [string]$name; Used = [int64]$_.UsedSpace; Max = [int64]$_.MaxSpace; Allocated = [int64]$_.AllocatedSpace }
  })
  $o.ShadowCopies = @(Get-CimInstance Win32_ShadowCopy).Count
  if(DEEP){ $o.CompStore = (Dism.exe /Online /Cleanup-Image /AnalyzeComponentStore /English) -join "` + "`n" + `" }
}
$o
`

func runHiddenHogs(c *core.Ctx) (*core.Result, error) {
	r := &core.Result{Title: "Hidden space hogs"}
	k := sys.Known()
	deep := "$false"
	if c.Bool("deep") {
		deep = "$true"
		c.Progress("analysing the component store (takes a minute)…")
	}
	var h hogsPS
	err := sys.PSJSON(c, strings.Replace(hogsScript, "DEEP", deep, 1), &h)
	if windowsOnly(r, err) {
		return r, nil
	}
	t := core.Table{Headers: []string{"What", "Size", "Note"}}
	row := func(what string, b int64, note string) {
		if b > 0 {
			t.Rows = append(t.Rows, []string{what, sys.HumanBytes(b), note})
		}
	}
	if h.Hiberfil > 0 {
		row("hiberfil.sys (hibernation)", h.Hiberfil, "RAM snapshot for hibernate / Fast Startup")
		r.Add(core.Finding{ID: "hiberfil", Severity: sevForBytes(h.Hiberfil), Title: "Hibernation file " + sys.HumanBytes(h.Hiberfil), Bytes: h.Hiberfil,
			Detail: "Windows reserves space equal to ~40–75% of your RAM for hibernation. Desktops rarely hibernate. 'Reduced' keeps Fast Startup and halves the file; 'off' removes it (and disables Fast Startup).",
			Fixes: []core.Fix{
				{ID: "reduce", Title: "Shrink hibernation file (keeps Fast Startup)", Risk: core.Safe, Admin: true, Reversible: true,
					Action: core.Action{Kind: core.ActExec, Cmd: []string{"powercfg", "/h", "/type", "reduced"}},
					Undo:   &core.Action{Kind: core.ActExec, Cmd: []string{"powercfg", "/h", "/type", "full"}}},
				{ID: "off", Title: "Turn hibernation off (frees all of it)", Risk: core.Moderate, Admin: true, Reversible: true,
					Action: core.Action{Kind: core.ActExec, Cmd: []string{"powercfg", "/h", "off"}},
					Undo:   &core.Action{Kind: core.ActExec, Cmd: []string{"powercfg", "/h", "on"}}},
			}})
	}
	row("pagefile.sys (virtual memory)", h.Pagefile, "Keep it — `memory` checks if its size is sensible")
	row("swapfile.sys", h.Swapfile, "Used by Store apps; small, keep")

	for _, spec := range []struct{ id, path, title, why string }{
		{"windows-old", k.SystemDrive + `\Windows.old`, "Previous Windows installation (Windows.old)", "Kept for 10 days after an upgrade so you can roll back. After that it is dead weight."},
	} {
		if !sys.Exists(spec.path) {
			continue
		}
		b, _ := sys.DirSize(c, spec.path)
		row(spec.title, b, "")
		r.Add(core.Finding{ID: spec.id, Severity: sevForBytes(b), Title: spec.title, Bytes: b, Detail: spec.why, Evidence: []string{spec.path},
			Fixes: []core.Fix{{ID: "remove", Title: "Remove Windows.old (no rollback afterwards)", Risk: core.Risky, Admin: true,
				Action: core.Action{Kind: core.ActPS, Script: "takeown /F \"$env:SystemDrive\\Windows.old\" /R /A /D Y | Out-Null\n" +
					"icacls \"$env:SystemDrive\\Windows.old\" /grant *S-1-5-32-544:F /T /C /Q | Out-Null\n" +
					"Remove-Item -LiteralPath \"$env:SystemDrive\\Windows.old\" -Recurse -Force"}}}})
	}

	for _, sh := range h.Shadow {
		row("Restore points / shadow copies on "+sh.Volume, sh.Used, fmt.Sprintf("max allowed %s, %d copies", sys.HumanBytes(sh.Max), h.ShadowCopies))
		if sh.Used > 15<<30 {
			r.Add(core.Finding{ID: "shadow-storage", Severity: core.Low, Title: "Restore points use " + sys.HumanBytes(sh.Used), Bytes: sh.Used - 8<<30,
				Detail: "System Protection keeps many old restore points. Capping it at ~8 GB keeps the last few while freeing the rest.",
				Fixes: []core.Fix{{ID: "cap", Title: "Cap restore point storage at 8 GB", Risk: core.Moderate, Admin: true,
					Action: core.Action{Kind: core.ActExec, Cmd: []string{"vssadmin", "resize", "shadowstorage", "/for=" + k.SystemDrive, "/on=" + k.SystemDrive, "/maxsize=8GB"}}}}})
		}
	}
	if strings.EqualFold(h.Reserved, "Enabled") {
		row("Reserved storage", 7<<30, "≈7 GB kept free for updates")
		r.Add(core.Finding{ID: "reserved-storage", Severity: core.Info, Title: "Reserved storage holds ~7 GB for updates", Bytes: 7 << 30,
			Detail: "Windows keeps ~7 GB aside so updates never fail for lack of space. Disabling gives it back, but updates may fail when the disk is nearly full.",
			Fixes: []core.Fix{{ID: "disable", Title: "Disable reserved storage", Risk: core.Moderate, Admin: true, Reversible: true,
				Action: core.Action{Kind: core.ActExec, Cmd: []string{"DISM", "/Online", "/Set-ReservedStorageState", "/State:Disabled"}},
				Undo:   &core.Action{Kind: core.ActExec, Cmd: []string{"DISM", "/Online", "/Set-ReservedStorageState", "/State:Enabled"}}}}})
	}

	comp := core.Finding{ID: "component-store", Severity: core.Low, Title: "Windows component store (WinSxS) cleanup",
		Detail: "WinSxS keeps superseded versions of system files after updates. DISM removes them safely. Never delete WinSxS by hand.",
		Fixes: []core.Fix{
			{ID: "cleanup", Title: "Remove superseded components (DISM, safe, slow)", Risk: core.Safe, Admin: true,
				Action: core.Action{Kind: core.ActExec, Cmd: []string{"Dism.exe", "/Online", "/Cleanup-Image", "/StartComponentCleanup"}}},
			{ID: "resetbase", Title: "Deep clean (installed updates can no longer be uninstalled)", Risk: core.Moderate, Admin: true,
				Action: core.Action{Kind: core.ActExec, Cmd: []string{"Dism.exe", "/Online", "/Cleanup-Image", "/StartComponentCleanup", "/ResetBase"}}},
		}}
	if h.CompStore != "" {
		comp.Evidence = []string{strings.TrimSpace(h.CompStore)}
		if strings.Contains(h.CompStore, "Cleanup Recommended : No") {
			comp.Severity = core.Info
			comp.Title += " — not needed right now"
		}
	}
	r.Add(comp)

	var vdisks []string
	for _, pat := range []string{`{local}\Packages\*\LocalState\ext4.vhdx`, `{local}\wsl\*\ext4.vhdx`, `{local}\Docker\wsl\*\*.vhdx`, `{local}\Docker\wsl\*.vhdx`} {
		vdisks = append(vdisks, sys.Glob(sys.ExpandKnown(pat))...)
	}
	for _, v := range vdisks {
		fi, err := os.Stat(v)
		if err != nil {
			continue
		}
		row("WSL/Docker virtual disk", fi.Size(), v)
		if fi.Size() > 10<<30 {
			script := fmt.Sprintf("wsl.exe --shutdown\nStart-Sleep 3\n$d = %s\n@(\"select vdisk file=`\"$d`\"\",'attach vdisk readonly','compact vdisk','detach vdisk') | Set-Content \"$env:TEMP\\ws-compact.txt\"\ndiskpart /s \"$env:TEMP\\ws-compact.txt\"", sys.PSQuote(v))
			r.Add(core.Finding{ID: "vdisk-" + shortHash(v), Severity: core.Low, Title: "Virtual disk " + filepath.Base(filepath.Dir(v)) + " is " + sys.HumanBytes(fi.Size()),
				Evidence: []string{v}, Data: map[string]any{"bytes": fi.Size()},
				Detail: "WSL/Docker disks grow but never shrink on their own. Compacting returns space freed inside Linux (delete files there first).",
				Fixes: []core.Fix{{ID: "compact", Title: "Shut down WSL and compact the virtual disk", Risk: core.Moderate, Admin: true,
					Action: core.Action{Kind: core.ActPS, Script: script}}}})
		}
	}
	for _, spec := range []struct{ pat, what string }{
		{`{local}\Microsoft\Outlook\*.ost`, "Outlook offline cache"},
		{`{roaming}\Apple Computer\MobileSync\Backup`, "iPhone backups"},
		{`{user}\Apple\MobileSync\Backup`, "iPhone backups"},
		{`{user}\.android\avd`, "Android emulators"},
		{`{user}\VirtualBox VMs`, "VirtualBox VMs"},
		{`{windows}\Installer`, "Windows Installer cache (never delete by hand)"},
	} {
		for _, p := range sys.Glob(sys.ExpandKnown(spec.pat)) {
			b, _ := sys.DirSize(c, p)
			row(spec.what, b, p)
		}
	}
	r.Tables = append(r.Tables, t)
	r.Summary = fmt.Sprintf("%d hidden space consumers found; %s reclaimable.", len(t.Rows), sys.HumanBytes(r.ReclaimableBytes()))
	adminNote(r)
	return r, nil
}
