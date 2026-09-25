package fix

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/coolman1984/performance/internal/core"
)

func TestCleanRespectsAgeAndKeepsRoot(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	root := filepath.Join(t.TempDir(), "cache")
	os.MkdirAll(filepath.Join(root, "sub"), 0o755)
	old := filepath.Join(root, "sub", "old.tmp")
	fresh := filepath.Join(root, "fresh.tmp")
	os.WriteFile(old, make([]byte, 1000), 0o644)
	os.WriteFile(fresh, make([]byte, 10), 0o644)
	past := time.Now().Add(-72 * time.Hour)
	os.Chtimes(old, past, past)

	fx := &core.Fix{ID: "t.x.clean", Action: core.Action{Kind: core.ActClean, Paths: []string{root}, OlderThanDays: 2}}
	if o := Apply(t.Context(), fx, true, nil); !o.OK || len(o.Plan) != 1 {
		t.Fatalf("dry run: %+v", o)
	}
	if _, err := os.Stat(old); err != nil {
		t.Fatal("dry run deleted a file")
	}
	o := Apply(t.Context(), fx, false, nil)
	if !o.OK || o.Freed != 1000 {
		t.Fatalf("apply: %+v", o)
	}
	if _, err := os.Stat(old); !os.IsNotExist(err) {
		t.Fatal("old file survived")
	}
	if _, err := os.Stat(fresh); err != nil {
		t.Fatal("fresh file was deleted")
	}
	if _, err := os.Stat(filepath.Join(root, "sub")); !os.IsNotExist(err) {
		t.Fatal("empty sub-directory not removed")
	}
	if _, err := os.Stat(root); err != nil {
		t.Fatal("root itself was removed")
	}
}

func TestRefusesProtectedFolders(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	home, _ := os.UserHomeDir()
	for _, p := range []string{"/", home} {
		fx := &core.Fix{ID: "t.x.y", Action: core.Action{Kind: core.ActClean, Paths: []string{p}}}
		if o := Apply(t.Context(), fx, false, nil); o.OK {
			t.Fatalf("cleaned protected folder %s", p)
		}
	}
}

func TestQuarantineAndUndo(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	f := filepath.Join(t.TempDir(), "broken.lnk")
	os.WriteFile(f, []byte("x"), 0o644)
	o := Apply(t.Context(), &core.Fix{ID: "t.q.move", Action: core.Action{Kind: core.ActQuarantine, Paths: []string{f}}}, false, nil)
	if !o.OK || o.JournalID == "" {
		t.Fatalf("quarantine: %+v", o)
	}
	if _, err := os.Stat(f); !os.IsNotExist(err) {
		t.Fatal("file not moved")
	}
	u := Undo(t.Context(), o.JournalID)
	if !u.OK {
		t.Fatalf("undo: %+v", u)
	}
	if _, err := os.Stat(f); err != nil {
		t.Fatal("file not restored")
	}
	entries, _ := Journal()
	if len(entries) != 2 || !entries[0].Undone {
		t.Fatalf("journal not updated: %+v", entries)
	}
	if again := Undo(t.Context(), o.JournalID); again.OK {
		t.Fatal("undo twice should fail")
	}
}

func TestAdminFixRefusedWhenNotElevated(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("running as root")
	}
	o := Apply(t.Context(), &core.Fix{ID: "t.a.b", Admin: true, Action: core.Action{Kind: core.ActExec, Cmd: []string{"true"}}}, false, nil)
	if o.OK {
		t.Fatal("admin fix ran without elevation")
	}
}
