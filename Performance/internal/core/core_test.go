package core

import (
	"reflect"
	"testing"
)

func TestParseArgs(t *testing.T) {
	pos, flags := ParseArgs([]string{`C:\`, "--top", "20", "--json", "--path=D:\\x", "extra"})
	if !reflect.DeepEqual(pos, []string{`C:\`, "extra"}) {
		t.Fatalf("pos = %v", pos)
	}
	if flags["top"] != "20" || flags["json"] != "true" || flags["path"] != `D:\x` {
		t.Fatalf("flags = %v", flags)
	}
}

func TestSplitCommandLine(t *testing.T) {
	got := SplitCommandLine(`topdirs "C:\Program Files" --all`)
	want := []string{"topdirs", `C:\Program Files`, "--all"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %q", got)
	}
}

func TestNamespace(t *testing.T) {
	r := &Result{Tool: "junk"}
	r.Add(Finding{ID: "temp", Fixes: []Fix{{ID: "clean"}}})
	r.Findings = append(r.Findings, Finding{ID: "other.x", Tool: "other", Fixes: []Fix{{ID: "other.x.y"}}})
	r.Namespace()
	if r.Findings[0].ID != "junk.temp" || r.Findings[0].Fixes[0].ID != "junk.temp.clean" || r.Findings[0].Tool != "junk" {
		t.Fatalf("namespacing failed: %+v", r.Findings[0])
	}
	if r.Findings[1].ID != "other.x" {
		t.Fatalf("copied finding was re-namespaced: %+v", r.Findings[1])
	}
}

func TestExecuteRecoversPanic(t *testing.T) {
	tool := &Tool{Name: "boom", Run: func(*Ctx) (*Result, error) { panic("x") }}
	r := Execute(tool, NewCtx(t.Context(), nil, nil, nil))
	if len(r.Errors) == 0 || r.Tool != "boom" {
		t.Fatalf("panic not recorded: %+v", r)
	}
}
