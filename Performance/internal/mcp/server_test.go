package mcp

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"

	"github.com/coolman1984/performance/internal/core"
)

func init() {
	core.Register(&core.Tool{Name: "echo", Category: "agent", Short: "test tool",
		Params: []core.Param{{Name: "n", Type: "int"}},
		Run: func(c *core.Ctx) (*core.Result, error) {
			r := &core.Result{Summary: "n=" + c.Str("n", "")}
			r.Add(core.Finding{ID: "f", Title: "hello", Fixes: []core.Fix{{ID: "noop", Risk: core.Safe, Action: core.Action{Kind: core.ActManual, Manual: "nothing"}}}})
			return r, nil
		}})
}

func TestProtocol(t *testing.T) {
	in := strings.Join([]string{
		`{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}`,
		`{"jsonrpc":"2.0","method":"notifications/initialized"}`,
		`{"jsonrpc":"2.0","id":2,"method":"tools/list"}`,
		`{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"echo","arguments":{"n":7}}}`,
		`{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"fix","arguments":{"ids":["echo.*"]}}}`,
		`{"jsonrpc":"2.0","id":5,"method":"nope"}`,
	}, "\n")
	var out bytes.Buffer
	if err := Serve(t.Context(), "test", strings.NewReader(in), &out); err != nil {
		t.Fatal(err)
	}
	byID := map[string]map[string]any{}
	for _, line := range strings.Split(strings.TrimSpace(out.String()), "\n") {
		var m map[string]any
		if err := json.Unmarshal([]byte(line), &m); err != nil {
			t.Fatalf("bad json %q", line)
		}
		id, _ := json.Marshal(m["id"])
		byID[string(id)] = m
	}
	if len(byID) != 5 {
		t.Fatalf("expected 5 responses (notification ignored), got %d: %s", len(byID), out.String())
	}
	if byID["1"]["result"].(map[string]any)["serverInfo"].(map[string]any)["name"] != "winsight" {
		t.Fatal("bad initialize")
	}
	tools := byID["2"]["result"].(map[string]any)["tools"].([]any)
	found := false
	for _, x := range tools {
		if x.(map[string]any)["name"] == "echo" {
			found = true
		}
	}
	if !found {
		t.Fatal("echo not listed")
	}
	txt := byID["3"]["result"].(map[string]any)["content"].([]any)[0].(map[string]any)["text"].(string)
	if !strings.Contains(txt, "n=7") || !strings.Contains(txt, "echo.f.noop") {
		t.Fatalf("call text: %s", txt)
	}
	fixTxt := byID["4"]["result"].(map[string]any)["content"].([]any)[0].(map[string]any)["text"].(string)
	if !strings.Contains(fixTxt, "preview") {
		t.Fatalf("fix preview: %s", fixTxt)
	}
	if byID["5"]["error"] == nil {
		t.Fatal("unknown method should error")
	}
}
