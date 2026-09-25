package core

import (
	"fmt"
	"runtime/debug"
	"sort"
	"strings"
	"sync"
	"time"
	"unicode"
)

var (
	mu    sync.RWMutex
	tools = map[string]*Tool{}
)

// Register adds a tool to the global catalogue. Called from init().
func Register(t *Tool) {
	mu.Lock()
	defer mu.Unlock()
	if _, dup := tools[t.Name]; dup {
		panic("duplicate tool " + t.Name)
	}
	tools[t.Name] = t
}

// Get finds a tool by name or alias.
func Get(name string) (*Tool, bool) {
	name = strings.ToLower(strings.TrimPrefix(name, "/"))
	mu.RLock()
	defer mu.RUnlock()
	if t, ok := tools[name]; ok {
		return t, true
	}
	for _, t := range tools {
		for _, a := range t.Aliases {
			if a == name {
				return t, true
			}
		}
	}
	return nil, false
}

// CategoryOrder is how tool groups are presented.
var CategoryOrder = []string{"space", "speed", "health", "repair", "secrets", "agent"}

// CategoryTitle is the heading shown for a group.
var CategoryTitle = map[string]string{
	"space":   "Free up space",
	"speed":   "Speed & startup",
	"health":  "Health & stability",
	"repair":  "Missing, broken & deleted",
	"secrets": "Windows secrets & hidden settings",
	"agent":   "Agent & automation",
}

// All returns tools sorted by category then name.
func All() []*Tool {
	mu.RLock()
	defer mu.RUnlock()
	rank := map[string]int{}
	for i, c := range CategoryOrder {
		rank[c] = i
	}
	out := make([]*Tool, 0, len(tools))
	for _, t := range tools {
		out = append(out, t)
	}
	sort.Slice(out, func(i, j int) bool {
		if rank[out[i].Category] != rank[out[j].Category] {
			return rank[out[i].Category] < rank[out[j].Category]
		}
		return out[i].Name < out[j].Name
	})
	return out
}

// BriefTools are the tools run by a full sweep.
func BriefTools() []*Tool {
	var out []*Tool
	for _, t := range All() {
		if t.InBrief {
			out = append(out, t)
		}
	}
	return out
}

// Execute runs a tool with timing and panic protection.
func Execute(t *Tool, c *Ctx) (res *Result) {
	start := time.Now()
	defer func() {
		if p := recover(); p != nil {
			res = &Result{Tool: t.Name, Title: t.Short}
			res.Errf("internal error: %v\n%s", p, debug.Stack())
		}
		if res == nil {
			res = &Result{Tool: t.Name, Title: t.Short}
		}
		res.Tool = t.Name
		res.Namespace()
		if res.Title == "" {
			res.Title = t.Short
		}
		res.Started = start
		res.Duration = time.Since(start)
		res.Millis = res.Duration.Milliseconds()
	}()
	r, err := t.Run(c)
	if r == nil {
		r = &Result{Tool: t.Name}
	}
	if err != nil {
		r.Errf("%v", err)
		if r.Summary == "" {
			r.Summary = "Could not complete: " + err.Error()
		}
	}
	return r
}

// ParseArgs turns ["C:\\", "--top", "20", "--json"] into positionals and flags.
func ParseArgs(argv []string) (pos []string, flags map[string]string) {
	flags = map[string]string{}
	for i := 0; i < len(argv); i++ {
		a := argv[i]
		if !strings.HasPrefix(a, "--") || len(a) == 2 {
			pos = append(pos, a)
			continue
		}
		k := strings.TrimPrefix(a, "--")
		if eq := strings.IndexByte(k, '='); eq >= 0 {
			flags[k[:eq]] = k[eq+1:]
			continue
		}
		if i+1 < len(argv) && !strings.HasPrefix(argv[i+1], "--") && !isBoolFlag(k) {
			flags[k] = argv[i+1]
			i++
			continue
		}
		flags[k] = "true"
	}
	return pos, flags
}

var boolFlags = map[string]bool{"json": true, "md": true, "yes": true, "y": true, "dry-run": true, "all": true, "elevate": true, "quiet": true, "no-color": true}

func isBoolFlag(k string) bool { return boolFlags[k] }

// SplitCommandLine splits a REPL line honouring double quotes, so Windows
// paths with spaces work: topdirs "C:\Program Files".
func SplitCommandLine(s string) []string {
	var out []string
	var cur strings.Builder
	inQ, has := false, false
	for _, r := range s {
		switch {
		case r == '"':
			inQ = !inQ
			has = true
		case unicode.IsSpace(r) && !inQ:
			if has {
				out = append(out, cur.String())
				cur.Reset()
				has = false
			}
		default:
			cur.WriteRune(r)
			has = true
		}
	}
	if has {
		out = append(out, cur.String())
	}
	return out
}

// Match finds the tool that best fits free text such as "why is my pc slow"
// or "وفر مساحة". It returns nil when nothing scores.
func Match(text string) (*Tool, int) {
	text = strings.ToLower(text)
	words := strings.FieldsFunc(text, func(r rune) bool { return !unicode.IsLetter(r) && !unicode.IsDigit(r) })
	var best *Tool
	bestScore := 0
	for _, t := range All() {
		score := 0
		for _, k := range t.Keywords {
			k = strings.ToLower(k)
			if strings.Contains(k, " ") {
				if strings.Contains(text, k) {
					score += 3
				}
				continue
			}
			for _, w := range words {
				if w == k {
					score += 2
				} else if len(k) >= 4 && strings.HasPrefix(w, k) {
					score++
				}
			}
		}
		for _, w := range words {
			if w == t.Name {
				score += 3
			}
		}
		if score > bestScore {
			best, bestScore = t, score
		}
	}
	return best, bestScore
}

// FindFix locates a fix by id inside results.
func FindFix(results []*Result, id string) (*Finding, *Fix) {
	for _, r := range results {
		for i := range r.Findings {
			f := &r.Findings[i]
			for j := range f.Fixes {
				if strings.EqualFold(f.Fixes[j].ID, id) {
					return f, &f.Fixes[j]
				}
			}
		}
	}
	return nil, nil
}

// ToolOfFix returns the tool that produces the given fix id.
func ToolOfFix(id string) (*Tool, error) {
	name, _, ok := strings.Cut(id, ".")
	if !ok {
		return nil, fmt.Errorf("fix id %q should look like tool.finding.fix", id)
	}
	t, found := Get(name)
	if !found {
		return nil, fmt.Errorf("no tool named %q (from fix id %q)", name, id)
	}
	return t, nil
}
