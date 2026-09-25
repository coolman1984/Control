// Package app holds the flows shared by the terminal UI, the one-shot CLI
// and the MCP server: finding fixes by id or wildcard, bulk clean-up and a
// short-lived cache of scan results.
package app

import (
	"context"
	"fmt"
	"path"
	"strings"
	"sync"
	"time"

	"github.com/coolman1984/performance/internal/core"
)

// Cache remembers recent results so `fix` doesn't re-scan what was just shown.
type Cache struct {
	mu   sync.Mutex
	byID map[string]*core.Result
}

// NewCache creates an empty cache.
func NewCache() *Cache { return &Cache{byID: map[string]*core.Result{}} }

// Put stores a result (and, for doctor, the sweep results inside it).
func (c *Cache) Put(r *core.Result) {
	if c == nil || r == nil {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	c.byID[r.Tool] = r
}

// PutAll stores many results.
func (c *Cache) PutAll(rs []*core.Result) {
	for _, r := range rs {
		c.Put(r)
	}
}

func (c *Cache) get(tool string, maxAge time.Duration) *core.Result {
	if c == nil {
		return nil
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	r := c.byID[tool]
	if r == nil || time.Since(r.Started) > maxAge {
		return nil
	}
	return r
}

// Run executes a tool and caches the result.
func (c *Cache) Run(ctx context.Context, t *core.Tool, pos []string, flags map[string]string, progress func(string)) *core.Result {
	r := core.Execute(t, core.NewCtx(ctx, pos, flags, progress))
	c.Put(r)
	return r
}

// Match reports whether a fix id matches a pattern such as "junk.*" or
// "tweaks.*.apply" (case-insensitive, * matches within and across dots).
func Match(pattern, id string) bool {
	pattern, id = strings.ToLower(pattern), strings.ToLower(id)
	if pattern == id {
		return true
	}
	if ok, _ := path.Match(strings.ReplaceAll(pattern, ".", "/"), strings.ReplaceAll(id, ".", "/")); ok {
		return true
	}
	// trailing ".*" also matches deeper ids: "junk.*" ⇒ "junk.user-temp.clean"
	if strings.HasSuffix(pattern, ".*") && strings.HasPrefix(id, strings.TrimSuffix(pattern, "*")) {
		return true
	}
	return false
}

// Resolve finds every fix matching the patterns, running (or reusing) the
// tools that produce them.
// flags (e.g. --path) are passed to tools that have to be run.
func (c *Cache) Resolve(ctx context.Context, patterns []string, flags map[string]string, progress func(string)) ([]core.Fix, error) {
	need := map[string]bool{}
	for _, p := range patterns {
		name, _, _ := strings.Cut(p, ".")
		if strings.ContainsAny(name, "*?") {
			for _, t := range core.BriefTools() {
				need[t.Name] = true
			}
			continue
		}
		if _, ok := core.Get(name); !ok {
			return nil, fmt.Errorf("no tool named %q (fix ids look like tool.finding.fix)", name)
		}
		need[name] = true
	}
	var results []*core.Result
	for name := range need {
		r := c.get(name, 15*time.Minute)
		if r == nil {
			t, _ := core.Get(name)
			if progress != nil {
				progress("scanning with " + name + " to locate the fix…")
			}
			r = c.Run(ctx, t, nil, toolFlags(flags), progress)
		}
		results = append(results, r)
	}
	var out []core.Fix
	seen := map[string]bool{}
	for _, r := range results {
		for _, f := range r.Findings {
			for _, fx := range f.Fixes {
				for _, p := range patterns {
					if Match(p, fx.ID) && !seen[fx.ID] {
						seen[fx.ID] = true
						out = append(out, fx)
					}
				}
			}
		}
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("no fix matches %s — it may already be fixed (re-run the tool to check)", strings.Join(patterns, ", "))
	}
	return out, nil
}

// CleanFixes returns every *safe* space-reclaiming fix from junk and devcache.
func (c *Cache) CleanFixes(ctx context.Context, progress func(string)) ([]core.Fix, int64) {
	var fixes []core.Fix
	var total int64
	for _, name := range []string{"junk", "devcache"} {
		r := c.get(name, 15*time.Minute)
		if r == nil {
			t, _ := core.Get(name)
			r = c.Run(ctx, t, nil, nil, progress)
		}
		for _, f := range r.Findings {
			if len(f.Fixes) == 0 || f.Fixes[0].Risk != core.Safe {
				continue
			}
			fixes = append(fixes, f.Fixes[0])
			total += f.Bytes
		}
	}
	return fixes, total
}

// SplitByAdmin separates fixes that need elevation when we aren't elevated.
func SplitByAdmin(fixes []core.Fix, admin bool) (runnable, needAdmin []core.Fix) {
	for _, f := range fixes {
		if f.Admin && !admin {
			needAdmin = append(needAdmin, f)
		} else {
			runnable = append(runnable, f)
		}
	}
	return
}

// toolFlags drops the flags that belong to the fix command itself.
func toolFlags(flags map[string]string) map[string]string {
	out := map[string]string{}
	for k, v := range flags {
		switch k {
		case "yes", "y", "json", "md", "quiet", "elevate", "pause", "all":
			continue
		}
		out[k] = v
	}
	return out
}
