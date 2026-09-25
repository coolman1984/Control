package tools

import (
	"fmt"

	"github.com/coolman1984/performance/internal/brief"
	"github.com/coolman1984/performance/internal/core"
	"github.com/coolman1984/performance/internal/sys"
)

func init() {
	core.Register(&core.Tool{Name: "doctor", Category: "agent",
		Short:    "Full check-up: runs every scan, scores the PC and lists the most important fixes",
		Aliases:  []string{"checkup", "scan", "all"},
		Keywords: []string{"doctor", "checkup", "check", "everything", "scan", "why slow", "fix my pc", "كشف", "افحص", "فحص", "كل", "شامل", "مشاكل", "مشكلة"},
		Run:      runDoctor})
	core.Register(&core.Tool{Name: "brief", Category: "agent",
		Short:    "Write the agent brief (brief.md + brief.json): everything an AI agent needs to fix this PC without researching",
		Params:   []core.Param{{Name: "out", Desc: "output folder", Default: "./winsight-brief-<time>"}},
		Aliases:  []string{"report"},
		Keywords: []string{"brief", "report", "agent", "export", "تقرير", "ايجنت", "الايجنت"},
		Run:      runBrief})
}

func runDoctor(c *core.Ctx) (*core.Result, error) {
	c.Progress("running every scan in parallel…")
	results := brief.Sweep(c, func(s string) { c.Progress("%s", s) })
	d := brief.Build(results)
	r := &core.Result{Title: "Check-up"}
	t := core.Table{Headers: []string{"Area", "Score"}}
	for _, cat := range core.CategoryOrder {
		if s, ok := d.Scores[cat]; ok {
			t.Rows = append(t.Rows, []string{core.CategoryTitle[cat], fmt.Sprintf("%d/100 %s", s, bar(s))})
		}
	}
	r.Tables = append(r.Tables, t)
	for i, f := range d.Top {
		if i == 15 {
			break
		}
		r.Findings = append(r.Findings, f)
	}
	for _, res := range results {
		for _, e := range res.Errors {
			r.Errors = append(r.Errors, res.Tool+": "+e)
		}
	}
	r.Data = d
	r.Summary = fmt.Sprintf("Health score %d/100 · %s reclaimable · %d findings (showing the top %d).", d.Score, sys.HumanBytes(d.Reclaim), len(brief.Ranked(results)), len(r.Findings))
	r.Notes = append(r.Notes, "`clean` reclaims all safe space in one go. `brief` saves the full report for an AI agent.")
	return r, nil
}

func bar(score int) string {
	n := score / 10
	out := ""
	for i := 0; i < 10; i++ {
		if i < n {
			out += "█"
		} else {
			out += "░"
		}
	}
	return out
}

func runBrief(c *core.Ctx) (*core.Result, error) {
	c.Progress("running every scan in parallel…")
	results := brief.Sweep(c, func(s string) { c.Progress("%s", s) })
	d := brief.Build(results)
	md, js, err := brief.Write(d, c.Str("out", ""))
	r := &core.Result{Title: "Agent brief"}
	if err != nil {
		return r, err
	}
	r.Summary = fmt.Sprintf("Score %d/100, %s reclaimable, %d top actions. Saved:\n  %s\n  %s", d.Score, sys.HumanBytes(d.Reclaim), len(d.Top), md, js)
	r.Data = map[string]any{"markdown": md, "json": js, "score": d.Score, "reclaimable_bytes": d.Reclaim}
	r.Notes = append(r.Notes, "Hand brief.md to your AI agent (Claude Code, Codex…) — it contains every finding, the exact fix command and the rules for applying them safely.")
	return r, nil
}
