// Package fix applies the actions attached to findings, journals every change
// and can undo reversible ones.
package fix

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"time"

	"github.com/coolman1984/performance/internal/core"
	"github.com/coolman1984/performance/internal/sys"
)

// Outcome describes what applying a fix did (or would do).
type Outcome struct {
	FixID     string   `json:"fix_id"`
	Title     string   `json:"title"`
	DryRun    bool     `json:"dry_run"`
	OK        bool     `json:"ok"`
	Freed     int64    `json:"freed_bytes,omitempty"`
	Plan      []string `json:"plan"`
	Output    string   `json:"output,omitempty"`
	Error     string   `json:"error,omitempty"`
	JournalID string   `json:"journal_id,omitempty"`
}

// ErrNeedsAdmin is returned when a fix requires elevation.
var ErrNeedsAdmin = errors.New("this fix needs administrator rights")

// Describe lists, in plain words, what an action will do.
func Describe(a core.Action) []string {
	var out []string
	switch a.Kind {
	case core.ActClean:
		age := ""
		if a.OlderThanDays > 0 {
			age = fmt.Sprintf(" (only files older than %d days)", a.OlderThanDays)
		}
		for _, p := range expandAll(a.Paths) {
			out = append(out, "empty "+p+age)
		}
	case core.ActDelete:
		for _, p := range expandAll(a.Paths) {
			out = append(out, "delete "+p)
		}
	case core.ActQuarantine:
		for _, p := range expandAll(a.Paths) {
			out = append(out, "move to quarantine (undoable): "+p)
		}
	case core.ActPS:
		out = append(out, "run PowerShell:\n"+indent(a.Script))
	case core.ActExec:
		out = append(out, "run: "+strings.Join(a.Cmd, " "))
	case core.ActManual:
		out = append(out, "manual step: "+a.Manual)
	}
	if len(out) == 0 {
		out = append(out, "nothing to do (paths no longer exist)")
	}
	return out
}

func indent(s string) string {
	lines := strings.Split(strings.TrimSpace(s), "\n")
	for i := range lines {
		lines[i] = "    " + lines[i]
	}
	return strings.Join(lines, "\n")
}

// Apply runs (or, with dry, previews) a fix.
func Apply(ctx context.Context, fx *core.Fix, dry bool, progress func(string)) Outcome {
	o := Outcome{FixID: fx.ID, Title: fx.Title, DryRun: dry, Plan: Describe(fx.Action)}
	if dry {
		o.OK = true
		return o
	}
	if fx.Action.Kind == core.ActManual {
		o.Error = "this fix is manual: " + fx.Action.Manual
		return o
	}
	if fx.Admin && !sys.IsAdmin() {
		o.Error = ErrNeedsAdmin.Error() + ` — run WinSight as administrator, or: winsight fix ` + fx.ID + ` --yes --elevate`
		return o
	}
	entry := Entry{ID: newID(), Time: time.Now(), FixID: fx.ID, Title: fx.Title, Action: fx.Action, Undo: fx.Undo}
	var err error
	switch fx.Action.Kind {
	case core.ActClean:
		o.Freed, err = clean(ctx, fx.Action.Paths, fx.Action.OlderThanDays, progress)
	case core.ActDelete:
		o.Freed, err = deletePaths(ctx, fx.Action.Paths)
	case core.ActQuarantine:
		entry.Moves, o.Freed, err = quarantine(entry.ID, fx.Action.Paths)
	case core.ActPS:
		o.Output, err = sys.PS(ctx, fx.Action.Script)
	case core.ActExec:
		if len(fx.Action.Cmd) == 0 {
			err = errors.New("empty command")
			break
		}
		o.Output, err = sys.Exec(ctx, fx.Action.Cmd[0], fx.Action.Cmd[1:]...)
	default:
		err = fmt.Errorf("unknown action kind %q", fx.Action.Kind)
	}
	o.Output = strings.TrimSpace(o.Output)
	o.OK = err == nil
	if err != nil {
		o.Error = err.Error()
		entry.Error = o.Error
	}
	entry.OK, entry.Freed = o.OK, o.Freed
	if jerr := appendJournal(entry); jerr == nil {
		o.JournalID = entry.ID
	}
	return o
}

func newID() string { return time.Now().Format("20060102-150405.000") }

func expandAll(paths []string) []string {
	var out []string
	for _, p := range paths {
		out = append(out, sys.Glob(sys.ExpandKnown(p))...)
	}
	return out
}

// dangerous refuses to empty or delete folders that must never be wiped.
func dangerous(p string) bool {
	p = strings.TrimRight(filepath.Clean(p), `\/`)
	if p == "" || p == "." || len(p) <= 3 { // "C:", "C:\", "/"
		return true
	}
	k := sys.Known()
	for _, bad := range []string{k.Windows, filepath.Join(k.Windows, "System32"), k.User, k.ProgramFiles, k.ProgramFilesX86,
		k.ProgramData, k.Local, k.Roaming, k.Desktop, k.Downloads, filepath.Join(k.User, "Documents"), filepath.Join(k.User, "Pictures"),
		filepath.Dir(k.User), os.Getenv("HOME"), "/usr", "/etc", "/home"} {
		if bad != "" && strings.EqualFold(p, strings.TrimRight(filepath.Clean(bad), `\/`)) {
			return true
		}
	}
	return false
}

func clean(ctx context.Context, patterns []string, olderDays int, progress func(string)) (int64, error) {
	cutoff := time.Now().Add(time.Duration(olderDays) * -24 * time.Hour)
	var freed int64
	for _, root := range expandAll(patterns) {
		if dangerous(root) {
			return freed, fmt.Errorf("refusing to empty protected folder %s", root)
		}
		if progress != nil {
			progress("cleaning " + root)
		}
		fi, err := os.Stat(root)
		if err != nil {
			continue
		}
		if !fi.IsDir() {
			if olderDays == 0 || fi.ModTime().Before(cutoff) {
				if os.Remove(root) == nil {
					freed += fi.Size()
				}
			}
			continue
		}
		sys.Walk(ctx, root, sys.WalkOptions{Workers: 8, OnFile: func(p string, size int64, mod time.Time) {
			if olderDays > 0 && !mod.Before(cutoff) {
				return
			}
			if os.Remove(p) == nil {
				atomic.AddInt64(&freed, size)
			}
		}})
		removeEmptyDirs(root, false)
	}
	return freed, ctx.Err()
}

// removeEmptyDirs deletes empty sub-directories bottom-up; root itself is
// kept unless self is true.
func removeEmptyDirs(dir string, self bool) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return
	}
	for _, e := range entries {
		if e.IsDir() && e.Type()&os.ModeSymlink == 0 {
			removeEmptyDirs(filepath.Join(dir, e.Name()), true)
		}
	}
	if self {
		_ = os.Remove(dir) // fails harmlessly when not empty
	}
}

func deletePaths(ctx context.Context, patterns []string) (int64, error) {
	var freed int64
	var errs []string
	for _, p := range expandAll(patterns) {
		if dangerous(p) {
			return freed, fmt.Errorf("refusing to delete protected folder %s", p)
		}
		n, _ := sys.DirSize(ctx, p)
		if err := os.RemoveAll(p); err != nil {
			errs = append(errs, err.Error())
			continue
		}
		freed += n
	}
	if len(errs) > 0 {
		return freed, errors.New(strings.Join(errs, "; "))
	}
	return freed, nil
}

// Dir is where WinSight keeps its journal and quarantine.
func Dir() string {
	base := os.Getenv("LOCALAPPDATA")
	if base == "" {
		base, _ = os.UserCacheDir()
	}
	d := filepath.Join(base, "WinSight")
	_ = os.MkdirAll(d, 0o755)
	return d
}

func quarantine(id string, patterns []string) ([]Move, int64, error) {
	qdir := filepath.Join(Dir(), "quarantine", id)
	if err := os.MkdirAll(qdir, 0o755); err != nil {
		return nil, 0, err
	}
	var moves []Move
	var total int64
	for i, p := range expandAll(patterns) {
		if dangerous(p) {
			return moves, total, fmt.Errorf("refusing to quarantine protected folder %s", p)
		}
		dst := filepath.Join(qdir, fmt.Sprintf("%03d_%s", i, filepath.Base(p)))
		n, _ := sys.DirSize(context.Background(), p)
		if err := move(p, dst); err != nil {
			return moves, total, err
		}
		moves = append(moves, Move{From: p, To: dst})
		total += n
	}
	return moves, total, nil
}

// move renames, falling back to copy+delete across volumes.
func move(src, dst string) error {
	if err := os.Rename(src, dst); err == nil {
		return nil
	}
	fi, err := os.Stat(src)
	if err != nil {
		return err
	}
	if fi.IsDir() {
		if err := copyDir(src, dst); err != nil {
			return err
		}
	} else if err := copyFile(src, dst, fi.Mode()); err != nil {
		return err
	}
	return os.RemoveAll(src)
}

func copyFile(src, dst string, mode os.FileMode) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
		return err
	}
	out, err := os.OpenFile(dst, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, mode)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		return err
	}
	return out.Close()
}

func copyDir(src, dst string) error {
	return filepath.Walk(src, func(p string, fi os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(src, p)
		t := filepath.Join(dst, rel)
		if fi.IsDir() {
			return os.MkdirAll(t, 0o755)
		}
		return copyFile(p, t, fi.Mode())
	})
}

// Move records a quarantine relocation.
type Move struct {
	From string `json:"from"`
	To   string `json:"to"`
}

// Entry is one line of the journal.
type Entry struct {
	ID     string       `json:"id"`
	Time   time.Time    `json:"time"`
	FixID  string       `json:"fix_id"`
	Title  string       `json:"title"`
	Action core.Action  `json:"action"`
	Undo   *core.Action `json:"undo,omitempty"`
	Moves  []Move       `json:"moves,omitempty"`
	Freed  int64        `json:"freed_bytes,omitempty"`
	OK     bool         `json:"ok"`
	Error  string       `json:"error,omitempty"`
	Undone bool         `json:"undone,omitempty"`
}

// Reversible reports whether Undo can revert this entry.
func (e Entry) Reversible() bool {
	return e.OK && !e.Undone && (len(e.Moves) > 0 || e.Undo != nil)
}

func journalPath() string { return filepath.Join(Dir(), "journal.jsonl") }

func appendJournal(e Entry) error {
	f, err := os.OpenFile(journalPath(), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	defer f.Close()
	b, _ := json.Marshal(e)
	_, err = f.Write(append(b, '\n'))
	return err
}

// Journal returns all entries, oldest first, with undo state folded in.
func Journal() ([]Entry, error) {
	f, err := os.Open(journalPath())
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var out []Entry
	undone := map[string]bool{}
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<24)
	for sc.Scan() {
		var e Entry
		if json.Unmarshal(sc.Bytes(), &e) != nil {
			continue
		}
		if strings.HasPrefix(e.FixID, "undo:") && e.OK {
			undone[strings.TrimPrefix(e.FixID, "undo:")] = true
		}
		out = append(out, e)
	}
	for i := range out {
		out[i].Undone = undone[out[i].ID]
	}
	return out, sc.Err()
}

// Undo reverts a journal entry.
func Undo(ctx context.Context, id string) Outcome {
	o := Outcome{FixID: "undo:" + id}
	entries, err := Journal()
	if err != nil {
		o.Error = err.Error()
		return o
	}
	var e *Entry
	for i := range entries {
		if entries[i].ID == id {
			e = &entries[i]
		}
	}
	if e == nil {
		o.Error = "no journal entry " + id
		return o
	}
	if !e.Reversible() {
		o.Error = "entry " + id + " cannot be undone (not reversible, failed, or already undone)"
		return o
	}
	o.Title = "Undo: " + e.Title
	var errs []string
	for _, m := range e.Moves {
		if err := move(m.To, m.From); err != nil {
			errs = append(errs, err.Error())
		}
		o.Plan = append(o.Plan, "restore "+m.From)
	}
	if e.Undo != nil {
		o.Plan = append(o.Plan, Describe(*e.Undo)...)
		switch e.Undo.Kind {
		case core.ActPS:
			o.Output, err = sys.PS(ctx, e.Undo.Script)
		case core.ActExec:
			o.Output, err = sys.Exec(ctx, e.Undo.Cmd[0], e.Undo.Cmd[1:]...)
		default:
			err = fmt.Errorf("cannot undo action kind %q", e.Undo.Kind)
		}
		if err != nil {
			errs = append(errs, err.Error())
		}
	}
	o.OK = len(errs) == 0
	o.Error = strings.Join(errs, "; ")
	_ = appendJournal(Entry{ID: newID(), Time: time.Now(), FixID: o.FixID, Title: o.Title, OK: o.OK, Error: o.Error})
	return o
}
