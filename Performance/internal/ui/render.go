// Package ui renders results for the terminal and runs the interactive
// Claude-Code-style shell and the live monitor.
package ui

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/lipgloss"

	"github.com/coolman1984/performance/internal/core"
	"github.com/coolman1984/performance/internal/fix"
	"github.com/coolman1984/performance/internal/sys"
)

// Palette.
var (
	cAccent = lipgloss.AdaptiveColor{Light: "#C15F3C", Dark: "#D97757"}
	cDim    = lipgloss.AdaptiveColor{Light: "#6B6B6B", Dark: "#8A8A8A"}
	cFaint  = lipgloss.AdaptiveColor{Light: "#A0A0A0", Dark: "#5C5C5C"}
	cText   = lipgloss.AdaptiveColor{Light: "#1F1F1F", Dark: "#E6E6E6"}
	cGreen  = lipgloss.AdaptiveColor{Light: "#2E8B57", Dark: "#7EC699"}
	cYellow = lipgloss.AdaptiveColor{Light: "#B8860B", Dark: "#E5C07B"}
	cRed    = lipgloss.AdaptiveColor{Light: "#C0392B", Dark: "#FF6B6B"}
	cOrange = lipgloss.AdaptiveColor{Light: "#D35400", Dark: "#FF9F5A"}
	cBlue   = lipgloss.AdaptiveColor{Light: "#2471A3", Dark: "#6CB6FF"}

	sAccent = lipgloss.NewStyle().Foreground(cAccent)
	sBold   = lipgloss.NewStyle().Bold(true).Foreground(cText)
	sDim    = lipgloss.NewStyle().Foreground(cDim)
	sFaint  = lipgloss.NewStyle().Foreground(cFaint)
	sGreen  = lipgloss.NewStyle().Foreground(cGreen)
	sYellow = lipgloss.NewStyle().Foreground(cYellow)
	sRed    = lipgloss.NewStyle().Foreground(cRed)
	sCode   = lipgloss.NewStyle().Foreground(cBlue)
)

var sevStyle = map[core.Severity]lipgloss.Style{
	core.Critical: lipgloss.NewStyle().Foreground(cRed).Bold(true),
	core.High:     lipgloss.NewStyle().Foreground(cOrange).Bold(true),
	core.Medium:   lipgloss.NewStyle().Foreground(cYellow),
	core.Low:      lipgloss.NewStyle().Foreground(cBlue),
	core.Info:     lipgloss.NewStyle().Foreground(cDim),
}

var sevIcon = map[core.Severity]string{core.Critical: "✖", core.High: "▲", core.Medium: "●", core.Low: "○", core.Info: "·"}
var sevLabel = map[core.Severity]string{core.Critical: "CRIT", core.High: "HIGH", core.Medium: "MED ", core.Low: "LOW ", core.Info: "INFO"}

func riskStyle(r core.Risk) lipgloss.Style {
	switch r {
	case core.Safe:
		return sGreen
	case core.Moderate:
		return sYellow
	}
	return sRed
}

// Options tune rendering.
type Options struct {
	Width int
	All   bool // show every finding and row
}

// Result renders a tool result.
func Result(r *core.Result, o Options) string {
	if o.Width <= 0 {
		o.Width = 100
	}
	var b strings.Builder
	head := sAccent.Render("◆ ") + sBold.Render(r.Tool) + "  " + sDim.Render(r.Title)
	dur := sFaint.Render(fmt.Sprintf("%.1fs", r.Duration.Seconds()))
	pad := o.Width - lipgloss.Width(head) - lipgloss.Width(dur) - 1
	if pad < 1 {
		pad = 1
	}
	b.WriteString(head + strings.Repeat(" ", pad) + dur + "\n")
	if r.Summary != "" {
		b.WriteString(indent(styleLines(sBold, r.Summary), 2))
	}
	for _, t := range r.Tables {
		b.WriteString("\n" + indent(Table(t, o.Width-2, o.All), 2))
	}
	if len(r.Findings) > 0 {
		b.WriteString("\n" + indent(Findings(r.Findings, o), 2))
	}
	for _, n := range r.Notes {
		b.WriteString("\n" + indent(sDim.Render("💡 "+n), 2))
	}
	for _, e := range r.Errors {
		b.WriteString("\n" + indent(sYellow.Render("⚠ "+sys.Truncate(firstLine(e), o.Width-6)), 2))
	}
	return strings.TrimRight(b.String(), "\n") + "\n"
}

func firstLine(s string) string {
	s, _, _ = strings.Cut(s, "\n")
	return s
}

// Findings renders a findings list with their fix ids.
func Findings(fs []core.Finding, o Options) string {
	var b strings.Builder
	shown, hiddenInfo, hidden := 0, 0, 0
	for _, f := range fs {
		if !o.All {
			if f.Severity == core.Info && shown >= 12 {
				hiddenInfo++
				continue
			}
			if shown >= 20 {
				hidden++
				continue
			}
		}
		shown++
		st := sevStyle[f.Severity]
		left := st.Render(sevIcon[f.Severity]+" "+sevLabel[f.Severity]) + "  " + sBold.Render(sys.Truncate(f.Title, o.Width-24))
		if f.Bytes > 0 {
			size := sAccent.Render(sys.HumanBytes(f.Bytes))
			pad := o.Width - 2 - lipgloss.Width(left) - lipgloss.Width(size)
			if pad < 2 {
				pad = 2
			}
			left += strings.Repeat(" ", pad) + size
		}
		b.WriteString(left + "\n")
		if f.Detail != "" {
			b.WriteString(wrap(sDim.Render(f.Detail), o.Width-10, 8))
		}
		for _, fx := range f.Fixes {
			tags := riskStyle(fx.Risk).Render(string(fx.Risk))
			if fx.Admin {
				tags += sFaint.Render(" · admin")
			}
			if fx.Reversible {
				tags += sFaint.Render(" · undoable")
			}
			b.WriteString(strings.Repeat(" ", 8) + sFaint.Render("↳ ") + sCode.Render("fix "+fx.ID) + "  " + tags + "\n")
		}
	}
	if hiddenInfo+hidden > 0 {
		b.WriteString(sFaint.Render(fmt.Sprintf("… %d more findings hidden — add --all to see everything", hiddenInfo+hidden)) + "\n")
	}
	return b.String()
}

// Table renders a grid that fits width, truncating the widest column.
func Table(t core.Table, width int, all bool) string {
	if len(t.Rows) == 0 {
		return ""
	}
	rows := t.Rows
	more := 0
	if !all && len(rows) > 25 {
		more = len(rows) - 25
		rows = rows[:25]
	}
	n := len(t.Headers)
	w := make([]int, n)
	for i, h := range t.Headers {
		w[i] = lipgloss.Width(h)
	}
	for _, r := range rows {
		for i := 0; i < n && i < len(r); i++ {
			if l := lipgloss.Width(r[i]); l > w[i] {
				w[i] = l
			}
		}
	}
	gap := 2
	total := func() int {
		s := 0
		for _, x := range w {
			s += x
		}
		return s + gap*(n-1)
	}
	for total() > width {
		max := 0
		for i := range w {
			if w[i] > w[max] {
				max = i
			}
		}
		if w[max] <= 8 {
			break
		}
		w[max]--
	}
	cell := func(s string, i int) string {
		s = sys.Truncate(s, w[i])
		return s + strings.Repeat(" ", max0(w[i]-lipgloss.Width(s)))
	}
	var b strings.Builder
	if t.Title != "" {
		b.WriteString(sAccent.Render(t.Title) + "\n")
	}
	var hs []string
	for i, h := range t.Headers {
		hs = append(hs, sDim.Render(cell(h, i)))
	}
	b.WriteString(strings.TrimRight(strings.Join(hs, "  "), " ") + "\n")
	b.WriteString(sFaint.Render(strings.Repeat("─", min(total(), width))) + "\n")
	for _, r := range rows {
		var cs []string
		for i := 0; i < n; i++ {
			v := ""
			if i < len(r) {
				v = r[i]
			}
			cs = append(cs, cell(v, i))
		}
		b.WriteString(strings.TrimRight(strings.Join(cs, "  "), " ") + "\n")
	}
	if more > 0 {
		b.WriteString(sFaint.Render(fmt.Sprintf("… %d more rows (--all)", more)) + "\n")
	}
	return b.String()
}

func max0(n int) int {
	if n < 0 {
		return 0
	}
	return n
}

func indent(s string, n int) string {
	p := strings.Repeat(" ", n)
	lines := strings.Split(strings.TrimRight(s, "\n"), "\n")
	for i, l := range lines {
		if l != "" {
			lines[i] = p + l
		}
	}
	return strings.Join(lines, "\n") + "\n"
}

// wrap word-wraps styled text and indents it.
func wrap(s string, width, ind int) string {
	if width < 20 {
		width = 20
	}
	lines := strings.Split(lipgloss.NewStyle().Width(width).Render(s), "\n")
	for i, l := range lines {
		lines[i] = strings.TrimRight(l, " ")
	}
	return indent(strings.Join(lines, "\n"), ind)
}

// Outcome renders the result of applying (or previewing) a fix.
func Outcome(o fix.Outcome) string {
	var b strings.Builder
	switch {
	case o.DryRun:
		b.WriteString(sYellow.Render("◇ preview ") + sCode.Render(o.FixID) + "  " + sDim.Render(o.Title) + "\n")
	case o.OK:
		b.WriteString(sGreen.Render("✔ done ") + sCode.Render(o.FixID) + "  " + sDim.Render(o.Title))
		if o.Freed > 0 {
			b.WriteString(sAccent.Render("  freed " + sys.HumanBytes(o.Freed)))
		}
		b.WriteString("\n")
	default:
		b.WriteString(sRed.Render("✖ failed ") + sCode.Render(o.FixID) + "  " + sDim.Render(o.Title) + "\n")
	}
	if o.DryRun {
		for _, p := range o.Plan {
			b.WriteString(indent(styleLines(sDim, "• "+p), 4))
		}
	}
	if o.Output != "" && !o.DryRun {
		b.WriteString(indent(styleLines(sFaint, sys.Truncate(o.Output, 600)), 4))
	}
	if o.Error != "" {
		b.WriteString(indent(styleLines(sRed, o.Error), 4))
	}
	if o.JournalID != "" && o.OK {
		b.WriteString(indent(sFaint.Render("journal "+o.JournalID), 4))
	}
	return b.String()
}

// styleLines styles each line separately so multi-line text isn't padded
// into a rectangle.
func styleLines(st lipgloss.Style, s string) string {
	lines := strings.Split(s, "\n")
	for i, l := range lines {
		lines[i] = st.Render(strings.TrimRight(l, " \r"))
	}
	return strings.Join(lines, "\n")
}

// Help lists every tool grouped by category.
func Help(width int) string {
	var b strings.Builder
	byCat := map[string][]*core.Tool{}
	for _, t := range core.All() {
		byCat[t.Category] = append(byCat[t.Category], t)
	}
	for _, c := range core.CategoryOrder {
		ts := byCat[c]
		if len(ts) == 0 {
			continue
		}
		b.WriteString("\n" + sAccent.Render(core.CategoryTitle[c]) + "\n")
		for _, t := range ts {
			name := sCode.Render(fmt.Sprintf("  /%-12s", t.Name))
			b.WriteString(name + " " + sDim.Render(sys.Truncate(t.Short, width-18)) + "\n")
		}
	}
	b.WriteString("\n" + sAccent.Render("Commands") + "\n")
	for _, c := range Commands {
		b.WriteString(sCode.Render(fmt.Sprintf("  /%-12s", c.Name)) + " " + sDim.Render(c.Desc) + "\n")
	}
	b.WriteString("\n" + sFaint.Render("  Type a tool name, a /command, or just describe the problem (\"my pc is slow\", \"وفر مساحة\").") + "\n")
	return b.String()
}

// Command is a built-in (non-tool) command.
type Command struct{ Name, Desc string }

// Commands are shared by the REPL, completion and help.
var Commands = []Command{
	{"fix", "Preview or apply fixes by id or wildcard: fix junk.* (asks before applying)"},
	{"clean", "Reclaim all safe space (junk + developer caches) in one go"},
	{"undo", "Undo a journaled change: undo <journal-id>"},
	{"journal", "Everything WinSight changed, with undo ids"},
	{"monitor", "Live dashboard: CPU, RAM, disk, network, top apps"},
	{"help", "This list"},
	{"clear", "Clear the screen"},
	{"exit", "Quit"},
}
