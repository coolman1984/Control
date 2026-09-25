// Package mcp exposes every WinSight tool to AI agents over the Model
// Context Protocol (JSON-RPC 2.0 on stdio), so Claude Code, Codex and other
// agents can call them directly instead of researching the machine by hand.
package mcp

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"strings"
	"sync"

	"github.com/coolman1984/performance/internal/app"
	"github.com/coolman1984/performance/internal/brief"
	"github.com/coolman1984/performance/internal/core"
	"github.com/coolman1984/performance/internal/fix"
	"github.com/coolman1984/performance/internal/sys"
)

type request struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id,omitempty"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params,omitempty"`
}

type rpcError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

type response struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id"`
	Result  any             `json:"result,omitempty"`
	Error   *rpcError       `json:"error,omitempty"`
}

// Server is a stdio MCP server.
type Server struct {
	version string
	out     io.Writer
	mu      sync.Mutex
	cache   *app.Cache
	wg      sync.WaitGroup
}

// Serve reads requests from in and writes responses to out until EOF.
func Serve(ctx context.Context, version string, in io.Reader, out io.Writer) error {
	s := &Server{version: version, out: out, cache: app.NewCache()}
	sc := bufio.NewScanner(in)
	sc.Buffer(make([]byte, 1<<20), 1<<26)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		var req request
		if err := json.Unmarshal([]byte(line), &req); err != nil {
			s.send(response{JSONRPC: "2.0", ID: json.RawMessage("null"), Error: &rpcError{-32700, "parse error"}})
			continue
		}
		if len(req.ID) == 0 { // notification
			continue
		}
		if req.Method == "tools/call" {
			s.wg.Add(1)
			go func(req request) {
				defer s.wg.Done()
				s.reply(req, s.call(ctx, req.Params))
			}(req)
			continue
		}
		s.reply(req, s.handle(req))
	}
	s.wg.Wait()
	return sc.Err()
}

func (s *Server) reply(req request, res any) {
	if e, ok := res.(*rpcError); ok {
		s.send(response{JSONRPC: "2.0", ID: req.ID, Error: e})
		return
	}
	s.send(response{JSONRPC: "2.0", ID: req.ID, Result: res})
}

func (s *Server) send(r response) {
	b, _ := json.Marshal(r)
	s.mu.Lock()
	defer s.mu.Unlock()
	_, _ = s.out.Write(append(b, '\n'))
}

const instructions = `WinSight is a Windows diagnostics toolkit that has already done the research for you.
Start with "doctor" (whole-PC triage with a ranked list of findings) or a specific tool (junk, startup, crashes, missing, recover…).
Every finding carries fix ids with a risk level (safe/moderate/risky), whether admin is needed and whether it is reversible.
Use "fix" with apply=false to preview and apply=true to apply. Apply safe fixes freely; ask the user before moderate; risky fixes (personal files) need confirm_risky=true and explicit user consent.
Everything applied is journaled and reversible fixes can be undone with "undo".`

func (s *Server) handle(req request) any {
	switch req.Method {
	case "initialize":
		var p struct {
			ProtocolVersion string `json:"protocolVersion"`
		}
		_ = json.Unmarshal(req.Params, &p)
		if p.ProtocolVersion == "" {
			p.ProtocolVersion = "2025-06-18"
		}
		return map[string]any{
			"protocolVersion": p.ProtocolVersion,
			"capabilities":    map[string]any{"tools": map[string]any{"listChanged": false}},
			"serverInfo":      map[string]any{"name": "winsight", "version": s.version},
			"instructions":    instructions,
		}
	case "ping":
		return map[string]any{}
	case "tools/list":
		return map[string]any{"tools": toolList()}
	case "resources/list":
		return map[string]any{"resources": []any{}}
	case "prompts/list":
		return map[string]any{"prompts": []any{}}
	}
	return &rpcError{-32601, "method not found: " + req.Method}
}

func schema(params []core.Param) map[string]any {
	props := map[string]any{}
	for _, p := range params {
		typ := p.Type
		switch typ {
		case "int":
			typ = "integer"
		case "bool":
			typ = "boolean"
		case "":
			typ = "string"
		}
		d := p.Desc
		if p.Default != "" {
			d += " (default: " + p.Default + ")"
		}
		props[p.Name] = map[string]any{"type": typ, "description": d}
	}
	return map[string]any{"type": "object", "properties": props}
}

func toolList() []map[string]any {
	var out []map[string]any
	for _, t := range core.All() {
		desc := t.Short
		if t.Long != "" {
			desc += ". " + t.Long
		}
		if t.Admin {
			desc += " Best run elevated."
		}
		out = append(out, map[string]any{
			"name": t.Name, "description": desc, "inputSchema": schema(t.Params),
			"annotations": map[string]any{"readOnlyHint": t.Name != "brief", "openWorldHint": false},
		})
	}
	out = append(out,
		map[string]any{"name": "fix", "description": "Preview (apply=false) or apply (apply=true) fixes by id or wildcard pattern such as 'junk.*' or 'tweaks.*.apply'. Returns what was done, bytes freed and journal ids for undo.",
			"inputSchema": map[string]any{"type": "object", "required": []string{"ids"}, "properties": map[string]any{
				"ids":           map[string]any{"type": "array", "items": map[string]any{"type": "string"}, "description": "fix ids or patterns"},
				"apply":         map[string]any{"type": "boolean", "description": "false = dry run (default), true = make the change"},
				"confirm_risky": map[string]any{"type": "boolean", "description": "required to apply risky fixes; only with explicit user consent"},
			}},
			"annotations": map[string]any{"destructiveHint": true}},
		map[string]any{"name": "clean", "description": "Preview or apply every SAFE space fix (junk + developer caches) at once.",
			"inputSchema": map[string]any{"type": "object", "properties": map[string]any{"apply": map[string]any{"type": "boolean"}}},
			"annotations": map[string]any{"destructiveHint": true}},
		map[string]any{"name": "undo", "description": "Undo a journaled change by journal id.",
			"inputSchema": map[string]any{"type": "object", "required": []string{"id"}, "properties": map[string]any{"id": map[string]any{"type": "string"}}}},
		map[string]any{"name": "journal", "description": "List everything WinSight changed on this PC, with journal ids.",
			"inputSchema": map[string]any{"type": "object", "properties": map[string]any{}}, "annotations": map[string]any{"readOnlyHint": true}},
	)
	return out
}

func text(s string, isErr bool, structured any) map[string]any {
	res := map[string]any{"content": []any{map[string]any{"type": "text", "text": s}}, "isError": isErr}
	if structured != nil {
		res["structuredContent"] = structured
	}
	return res
}

func (s *Server) call(ctx context.Context, raw json.RawMessage) any {
	var p struct {
		Name      string         `json:"name"`
		Arguments map[string]any `json:"arguments"`
	}
	if err := json.Unmarshal(raw, &p); err != nil {
		return &rpcError{-32602, "invalid params"}
	}
	args := map[string]string{}
	for k, v := range p.Arguments {
		switch x := v.(type) {
		case string:
			args[k] = x
		case bool:
			args[k] = fmt.Sprint(x)
		case float64:
			args[k] = fmt.Sprint(int64(x))
		}
	}
	switch p.Name {
	case "fix":
		var ids []string
		if arr, ok := p.Arguments["ids"].([]any); ok {
			for _, v := range arr {
				ids = append(ids, fmt.Sprint(v))
			}
		} else if one, ok := p.Arguments["ids"].(string); ok {
			ids = strings.Fields(one)
		}
		fixes, err := s.cache.Resolve(ctx, ids, nil, nil)
		if err != nil {
			return text(err.Error(), true, nil)
		}
		return s.applyFixes(ctx, fixes, args["apply"] == "true", args["confirm_risky"] == "true")
	case "clean":
		fixes, _ := s.cache.CleanFixes(ctx, nil)
		return s.applyFixes(ctx, fixes, args["apply"] == "true", false)
	case "undo":
		o := fix.Undo(ctx, args["id"])
		b, _ := json.MarshalIndent(o, "", "  ")
		return text(string(b), !o.OK, o)
	case "journal":
		entries, err := fix.Journal()
		if err != nil {
			return text(err.Error(), true, nil)
		}
		b, _ := json.MarshalIndent(entries, "", "  ")
		return text(string(b), false, map[string]any{"entries": entries})
	}
	t, ok := core.Get(p.Name)
	if !ok {
		return text("unknown tool "+p.Name, true, nil)
	}
	r := s.cache.Run(ctx, t, nil, args, nil)
	if d, ok := r.Data.(brief.Doc); ok {
		s.cache.PutAll(d.Results)
		return text(brief.Markdown(d), false, nil)
	}
	return text(brief.ResultMarkdown(r), false, r)
}

func (s *Server) applyFixes(ctx context.Context, fixes []core.Fix, apply, confirmRisky bool) any {
	var outs []fix.Outcome
	var b strings.Builder
	admin := sys.IsAdmin()
	for i := range fixes {
		fx := &fixes[i]
		switch {
		case !apply:
			outs = append(outs, fix.Apply(ctx, fx, true, nil))
		case fx.Risk == core.Risky && !confirmRisky:
			outs = append(outs, fix.Outcome{FixID: fx.ID, Title: fx.Title, Error: "risky fix skipped: ask the user, then call again with confirm_risky=true"})
		case fx.Admin && !admin:
			outs = append(outs, fix.Outcome{FixID: fx.ID, Title: fx.Title, Error: "needs administrator: ask the user to run `winsight fix " + fx.ID + " --yes --elevate` or start the MCP server elevated"})
		default:
			outs = append(outs, fix.Apply(ctx, fx, false, nil))
		}
	}
	var freed int64
	for _, o := range outs {
		state := "preview"
		switch {
		case o.Error != "":
			state = "FAILED/SKIPPED: " + o.Error
		case !o.DryRun:
			state = "done"
		}
		fmt.Fprintf(&b, "- `%s` %s — %s", o.FixID, o.Title, state)
		if o.Freed > 0 {
			fmt.Fprintf(&b, " (freed %s)", sys.HumanBytes(o.Freed))
			freed += o.Freed
		}
		if o.JournalID != "" {
			fmt.Fprintf(&b, " [journal %s]", o.JournalID)
		}
		b.WriteString("\n")
		if o.DryRun {
			for _, p := range o.Plan {
				b.WriteString("    " + strings.ReplaceAll(p, "\n", "\n    ") + "\n")
			}
		}
	}
	if freed > 0 {
		fmt.Fprintf(&b, "\nTotal freed: %s\n", sys.HumanBytes(freed))
	}
	return text(b.String(), false, map[string]any{"outcomes": outs})
}
