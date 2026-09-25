// Package core holds the shared vocabulary of WinSight: tools, results,
// findings and the fix actions that an operator (human or AI agent) can apply.
package core

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"
)

// Severity ranks how much a finding matters.
type Severity int

const (
	Info Severity = iota
	Low
	Medium
	High
	Critical
)

var severityNames = []string{"info", "low", "medium", "high", "critical"}

func (s Severity) String() string {
	if s < Info || s > Critical {
		return "info"
	}
	return severityNames[s]
}

func (s Severity) MarshalJSON() ([]byte, error) { return json.Marshal(s.String()) }

func (s *Severity) UnmarshalJSON(b []byte) error {
	var v string
	if err := json.Unmarshal(b, &v); err != nil {
		return err
	}
	for i, n := range severityNames {
		if n == v {
			*s = Severity(i)
			return nil
		}
	}
	return fmt.Errorf("unknown severity %q", v)
}

// Risk tells the operator how careful to be before applying a fix.
type Risk string

const (
	Safe     Risk = "safe"     // only regenerable data or trivially reversible
	Moderate Risk = "moderate" // reversible, but changes behaviour
	Risky    Risk = "risky"    // may lose data or break things; needs a human
)

// Action kinds understood by the fix executor.
const (
	ActClean      = "clean"      // delete the *contents* of Paths (optionally only files older than N days)
	ActDelete     = "delete"     // delete Paths themselves
	ActQuarantine = "quarantine" // move Paths into the WinSight quarantine (undoable)
	ActPS         = "powershell" // run Script with Windows PowerShell
	ActExec       = "exec"       // run Cmd[0] with Cmd[1:]
	ActManual     = "manual"     // cannot be automated: follow Manual instructions
)

// Action is a concrete, machine-executable change.
type Action struct {
	Kind          string   `json:"kind"`
	Paths         []string `json:"paths,omitempty"`
	OlderThanDays int      `json:"older_than_days,omitempty"`
	Script        string   `json:"script,omitempty"`
	Cmd           []string `json:"cmd,omitempty"`
	Manual        string   `json:"manual,omitempty"`
}

// Fix is one way to resolve a finding.
type Fix struct {
	ID         string  `json:"id"` // globally unique: <tool>.<finding>.<fix>
	Title      string  `json:"title"`
	Risk       Risk    `json:"risk"`
	Admin      bool    `json:"needs_admin"`
	Reversible bool    `json:"reversible"`
	Action     Action  `json:"action"`
	Undo       *Action `json:"undo,omitempty"`
	Verify     string  `json:"verify,omitempty"` // PowerShell one-liner that proves it worked
}

// Finding is one observation about the machine.
type Finding struct {
	ID       string         `json:"id"` // <tool>.<slug>
	Tool     string         `json:"tool"`
	Severity Severity       `json:"severity"`
	Title    string         `json:"title"`
	Detail   string         `json:"detail,omitempty"`
	Bytes    int64          `json:"reclaimable_bytes,omitempty"`
	Evidence []string       `json:"evidence,omitempty"`
	Data     map[string]any `json:"data,omitempty"`
	Fixes    []Fix          `json:"fixes,omitempty"`
}

// Table is a small grid rendered in the terminal and in reports.
type Table struct {
	Title   string     `json:"title,omitempty"`
	Headers []string   `json:"headers"`
	Rows    [][]string `json:"rows"`
}

// Result is what every tool returns.
type Result struct {
	Tool     string        `json:"tool"`
	Title    string        `json:"title"`
	Summary  string        `json:"summary"`
	Findings []Finding     `json:"findings,omitempty"`
	Tables   []Table       `json:"tables,omitempty"`
	Data     any           `json:"data,omitempty"`
	Notes    []string      `json:"notes,omitempty"`
	Errors   []string      `json:"errors,omitempty"`
	Duration time.Duration `json:"-"`
	Millis   int64         `json:"duration_ms"`
	Started  time.Time     `json:"started"`
}

// ReclaimableBytes sums the space every finding could free.
func (r *Result) ReclaimableBytes() int64 {
	var n int64
	for _, f := range r.Findings {
		n += f.Bytes
	}
	return n
}

// Add appends a finding, stamping the tool name and namespacing ids.
// Ids are finalised by Namespace once the tool has returned.
func (r *Result) Add(f Finding) {
	r.Findings = append(r.Findings, f)
}

// Namespace stamps the tool name on findings added by this tool and makes
// finding and fix ids globally unique (<tool>.<finding>.<fix>). Findings
// copied from other results (already stamped) are left untouched.
func (r *Result) Namespace() {
	for i := range r.Findings {
		f := &r.Findings[i]
		if f.Tool != "" {
			continue
		}
		f.Tool = r.Tool
		if !strings.HasPrefix(f.ID, r.Tool+".") {
			f.ID = r.Tool + "." + f.ID
		}
		for j := range f.Fixes {
			if !strings.HasPrefix(f.Fixes[j].ID, f.ID+".") {
				f.Fixes[j].ID = f.ID + "." + f.Fixes[j].ID
			}
		}
	}
}

// Errf records a non-fatal problem the tool hit.
func (r *Result) Errf(format string, a ...any) {
	r.Errors = append(r.Errors, fmt.Sprintf(format, a...))
}

// Param documents a tool argument (used for help, completion and MCP schemas).
type Param struct {
	Name    string `json:"name"`
	Desc    string `json:"description"`
	Default string `json:"default,omitempty"`
	Type    string `json:"type,omitempty"` // string | int | bool (default string)
}

// Ctx is handed to a running tool.
type Ctx struct {
	context.Context
	Args     map[string]string
	Pos      []string
	progress func(string)
}

// NewCtx builds a tool context. progress may be nil.
func NewCtx(ctx context.Context, pos []string, args map[string]string, progress func(string)) *Ctx {
	if args == nil {
		args = map[string]string{}
	}
	return &Ctx{Context: ctx, Args: args, Pos: pos, progress: progress}
}

// Progress reports a human-readable status line while the tool works.
func (c *Ctx) Progress(format string, a ...any) {
	if c.progress != nil {
		c.progress(fmt.Sprintf(format, a...))
	}
}

// Str returns a named argument, the first positional argument when name is
// the tool's primary parameter, or def.
func (c *Ctx) Str(name, def string) string {
	if v, ok := c.Args[name]; ok && v != "" {
		return v
	}
	return def
}

// Int returns a named integer argument or def.
func (c *Ctx) Int(name string, def int) int {
	v, ok := c.Args[name]
	if !ok {
		return def
	}
	var n int
	if _, err := fmt.Sscanf(v, "%d", &n); err != nil {
		return def
	}
	return n
}

// Bool returns a named flag.
func (c *Ctx) Bool(name string) bool {
	v, ok := c.Args[name]
	return ok && v != "false" && v != "0" && v != "no"
}

// Tool is a small, specialised diagnostic.
type Tool struct {
	Name     string
	Category string
	Short    string
	Long     string
	Aliases  []string
	Keywords []string // natural-language hints (English and Arabic)
	Params   []Param
	Admin    bool // gives better results when elevated
	Slow     bool // walks big trees or waits on Windows
	InBrief  bool // part of the full `brief` / `doctor` sweep
	Run      func(c *Ctx) (*Result, error)
}
