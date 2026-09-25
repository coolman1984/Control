// Package brief runs the full sweep and turns it into an "agent brief": a
// ranked, self-contained Markdown + JSON report that tells an AI agent (or a
// human) exactly what is wrong and the exact command that fixes each thing.
package brief

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/coolman1984/performance/internal/core"
	"github.com/coolman1984/performance/internal/sys"
)

// Version is stamped into reports.
var Version = "dev"

// Sweep runs every brief tool in parallel.
func Sweep(ctx context.Context, progress func(string)) []*core.Result {
	tools := core.BriefTools()
	if t, ok := core.Get("sysinfo"); ok {
		tools = append([]*core.Tool{t}, tools...)
	}
	results := make([]*core.Result, len(tools))
	var wg sync.WaitGroup
	sem := make(chan struct{}, 4)
	var mu sync.Mutex
	done := 0
	for i, t := range tools {
		wg.Add(1)
		go func(i int, t *core.Tool) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()
			results[i] = core.Execute(t, core.NewCtx(ctx, nil, nil, nil))
			mu.Lock()
			done++
			if progress != nil {
				progress(fmt.Sprintf("[%d/%d] %s done", done, len(tools), t.Name))
			}
			mu.Unlock()
		}(i, t)
	}
	wg.Wait()
	return results
}

var penalty = map[core.Severity]float64{core.Critical: 20, core.High: 9, core.Medium: 4, core.Low: 1.5}

// Score turns findings into a 0–100 health score, overall and per category.
func Score(results []*core.Result) (int, map[string]int) {
	cats := map[string]float64{}
	var total float64
	for _, r := range results {
		t, _ := core.Get(r.Tool)
		cat := "other"
		if t != nil {
			cat = t.Category
		}
		if _, ok := cats[cat]; !ok {
			cats[cat] = 0
		}
		for _, f := range r.Findings {
			cats[cat] += penalty[f.Severity]
			total += penalty[f.Severity]
		}
	}
	clamp := func(p float64) int {
		s := 100 - p
		if s < 0 {
			s = 0
		}
		return int(s)
	}
	out := map[string]int{}
	for c, p := range cats {
		out[c] = clamp(p * 1.5)
	}
	return clamp(total / 1.5), out
}

// Ranked returns all findings, most important first.
func Ranked(results []*core.Result) []core.Finding {
	var all []core.Finding
	for _, r := range results {
		all = append(all, r.Findings...)
	}
	sort.SliceStable(all, func(i, j int) bool {
		if all[i].Severity != all[j].Severity {
			return all[i].Severity > all[j].Severity
		}
		return all[i].Bytes > all[j].Bytes
	})
	return all
}

// Doc is the JSON brief.
type Doc struct {
	Tool      string         `json:"generator"`
	Version   string         `json:"version"`
	Generated time.Time      `json:"generated"`
	Host      string         `json:"host"`
	Admin     bool           `json:"admin"`
	Score     int            `json:"score"`
	Scores    map[string]int `json:"category_scores"`
	Reclaim   int64          `json:"reclaimable_bytes"`
	Top       []core.Finding `json:"top_findings"`
	Results   []*core.Result `json:"results"`
	Guide     []string       `json:"agent_rules"`
}

// AgentRules tell an AI agent how to act on the brief safely.
var AgentRules = []string{
	"Everything in this brief is already researched: do not re-investigate, act on the fix ids.",
	"Preview any fix with `winsight fix <id>` (dry run), apply with `winsight fix <id> --yes`. Add --json for machine-readable output.",
	"`safe` fixes may be applied without asking. Ask the user before `moderate`, and always get explicit consent for `risky` (these touch personal files).",
	"Fixes with needs_admin=true require an elevated terminal (or `--elevate`, which shows a UAC prompt to the user).",
	"Before applying moderate/risky registry, service or driver fixes, create a restore point: `winsight fix recover.safety-point.create --yes` (admin).",
	"Every applied fix is journaled; reversible ones can be undone with `winsight undo <journal-id>`. See `winsight journal`.",
	"Wildcards work: `winsight fix \"junk.*\" --yes` applies all junk fixes; `winsight clean --yes` applies every safe space fix.",
	"After fixing, re-run the tool named before the first dot of the fix id to verify the finding is gone.",
}

// Build assembles the JSON document.
func Build(results []*core.Result) Doc {
	host, _ := os.Hostname()
	score, cats := Score(results)
	var reclaim int64
	for _, r := range results {
		reclaim += r.ReclaimableBytes()
	}
	ranked := Ranked(results)
	var top []core.Finding
	for _, f := range ranked {
		if f.Severity >= core.Low || f.Bytes > 1<<30 {
			top = append(top, f)
		}
		if len(top) == 25 {
			break
		}
	}
	return Doc{Tool: "WinSight", Version: Version, Generated: time.Now(), Host: host, Admin: sys.IsAdmin(),
		Score: score, Scores: cats, Reclaim: reclaim, Top: top, Results: results, Guide: AgentRules}
}

// Markdown renders the brief for agents and humans.
func Markdown(d Doc) string {
	var b strings.Builder
	w := func(format string, a ...any) { fmt.Fprintf(&b, format, a...) }
	w("# WinSight agent brief — %s\n\n", d.Host)
	w("Generated %s by WinSight %s · admin: %v · read-only scan, nothing was changed.\n\n", d.Generated.Format("2006-01-02 15:04"), d.Version, d.Admin)
	w("## Score: %d/100\n\n", d.Score)
	var cats []string
	for c := range d.Scores {
		cats = append(cats, c)
	}
	sort.Strings(cats)
	for _, c := range cats {
		title := core.CategoryTitle[c]
		if title == "" {
			title = c
		}
		w("- %s: **%d**\n", title, d.Scores[c])
	}
	w("\nReclaimable space found: **%s**\n\n", sys.HumanBytes(d.Reclaim))
	w("## Rules for the agent\n\n")
	for _, g := range d.Guide {
		w("- %s\n", g)
	}
	for _, r := range d.Results {
		if r.Tool == "sysinfo" {
			w("\n## Machine\n\n")
			for _, t := range r.Tables {
				writeTable(&b, t)
			}
		}
	}
	w("\n## Top actions\n\n")
	for i, f := range d.Top {
		w("### %d. [%s] %s\n\n", i+1, strings.ToUpper(f.Severity.String()), f.Title)
		if f.Bytes > 0 {
			w("Reclaims **%s**. ", sys.HumanBytes(f.Bytes))
		}
		if f.Detail != "" {
			w("%s\n\n", f.Detail)
		}
		writeEvidence(&b, f.Evidence)
		writeFixes(&b, f.Fixes)
	}
	w("\n## Full results\n")
	for _, r := range d.Results {
		w("\n### `%s` — %s\n\n%s\n\n", r.Tool, r.Title, r.Summary)
		for _, t := range r.Tables {
			writeTable(&b, t)
		}
		for _, n := range r.Notes {
			w("> %s\n\n", n)
		}
		for _, e := range r.Errors {
			w("> ⚠ %s\n\n", sys.Truncate(e, 300))
		}
		for _, f := range r.Findings {
			line := fmt.Sprintf("- **[%s]** %s", f.Severity, f.Title)
			if f.Bytes > 0 {
				line += " — " + sys.HumanBytes(f.Bytes)
			}
			var ids []string
			for _, fx := range f.Fixes {
				ids = append(ids, fmt.Sprintf("`%s` (%s)", fx.ID, fx.Risk))
			}
			if len(ids) > 0 {
				line += " → fix: " + strings.Join(ids, ", ")
			}
			w("%s\n", line)
		}
	}
	return b.String()
}

func writeEvidence(b *strings.Builder, ev []string) {
	if len(ev) == 0 {
		return
	}
	b.WriteString("Evidence:\n\n```\n")
	for i, e := range ev {
		if i == 15 {
			fmt.Fprintf(b, "… and %d more\n", len(ev)-15)
			break
		}
		b.WriteString(sys.Truncate(e, 400) + "\n")
	}
	b.WriteString("```\n\n")
}

func writeFixes(b *strings.Builder, fixes []core.Fix) {
	for _, fx := range fixes {
		flags := []string{string(fx.Risk)}
		if fx.Admin {
			flags = append(flags, "admin")
		}
		if fx.Reversible {
			flags = append(flags, "reversible")
		}
		fmt.Fprintf(b, "- **%s** (%s)\n  - run: `winsight fix %s --yes`\n", fx.Title, strings.Join(flags, ", "), fx.ID)
		switch fx.Action.Kind {
		case core.ActPS:
			fmt.Fprintf(b, "  - does:\n\n```powershell\n%s\n```\n", strings.TrimSpace(fx.Action.Script))
		case core.ActExec:
			fmt.Fprintf(b, "  - does: `%s`\n", strings.Join(fx.Action.Cmd, " "))
		case core.ActClean:
			fmt.Fprintf(b, "  - does: empties %s", strings.Join(fx.Action.Paths, ", "))
			if fx.Action.OlderThanDays > 0 {
				fmt.Fprintf(b, " (files older than %d days)", fx.Action.OlderThanDays)
			}
			b.WriteString("\n")
		case core.ActDelete, core.ActQuarantine:
			fmt.Fprintf(b, "  - does: %s %d path(s)\n", fx.Action.Kind, len(fx.Action.Paths))
		case core.ActManual:
			fmt.Fprintf(b, "  - manual: %s\n", fx.Action.Manual)
		}
	}
	b.WriteString("\n")
}

func writeTable(b *strings.Builder, t core.Table) {
	if len(t.Rows) == 0 {
		return
	}
	if t.Title != "" {
		fmt.Fprintf(b, "**%s**\n\n", t.Title)
	}
	esc := func(s string) string { return strings.ReplaceAll(strings.ReplaceAll(s, "|", "\\|"), "\n", " ") }
	h := make([]string, len(t.Headers))
	for i, x := range t.Headers {
		h[i] = esc(x)
	}
	fmt.Fprintf(b, "| %s |\n|%s\n", strings.Join(h, " | "), strings.Repeat(" --- |", len(t.Headers)))
	for _, row := range t.Rows {
		cells := make([]string, len(row))
		for i, x := range row {
			cells[i] = esc(x)
		}
		fmt.Fprintf(b, "| %s |\n", strings.Join(cells, " | "))
	}
	b.WriteString("\n")
}

// Write saves brief.md and brief.json into dir and returns their paths.
func Write(d Doc, dir string) (string, string, error) {
	if dir == "" {
		dir = filepath.Join(".", "winsight-brief-"+d.Generated.Format("20060102-1504"))
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", "", err
	}
	md := filepath.Join(dir, "brief.md")
	js := filepath.Join(dir, "brief.json")
	if err := os.WriteFile(md, []byte(Markdown(d)), 0o644); err != nil {
		return "", "", err
	}
	raw, _ := json.MarshalIndent(d, "", "  ")
	if err := os.WriteFile(js, raw, 0o644); err != nil {
		return "", "", err
	}
	return md, js, nil
}

// ResultMarkdown renders one result compactly (used by the MCP server).
func ResultMarkdown(r *core.Result) string {
	var b strings.Builder
	fmt.Fprintf(&b, "## %s — %s\n\n%s\n\n", r.Tool, r.Title, r.Summary)
	for _, t := range r.Tables {
		writeTable(&b, t)
	}
	for _, f := range r.Findings {
		fmt.Fprintf(&b, "### [%s] %s\n\n", strings.ToUpper(f.Severity.String()), f.Title)
		if f.Bytes > 0 {
			fmt.Fprintf(&b, "Reclaims %s. ", sys.HumanBytes(f.Bytes))
		}
		if f.Detail != "" {
			b.WriteString(f.Detail + "\n\n")
		}
		writeEvidence(&b, f.Evidence)
		writeFixes(&b, f.Fixes)
	}
	for _, n := range r.Notes {
		fmt.Fprintf(&b, "> %s\n\n", n)
	}
	for _, e := range r.Errors {
		fmt.Fprintf(&b, "> warning: %s\n\n", sys.Truncate(e, 300))
	}
	return b.String()
}
