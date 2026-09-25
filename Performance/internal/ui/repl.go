package ui

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/spinner"
	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/shirou/gopsutil/v4/disk"

	"github.com/coolman1984/performance/internal/app"
	"github.com/coolman1984/performance/internal/brief"
	"github.com/coolman1984/performance/internal/core"
	"github.com/coolman1984/performance/internal/fix"
	"github.com/coolman1984/performance/internal/sys"
)

type mode int

const (
	modeInput mode = iota
	modeRunning
	modeConfirm
	modeMonitor
)

type suggestion struct{ name, desc string }

// Model is the interactive shell.
type Model struct {
	ti       textinput.Model
	spin     spinner.Model
	width    int
	height   int
	mode     mode
	status   string
	running  string
	started  time.Time
	cancel   context.CancelFunc
	history  []string
	hIdx     int
	sugg     []suggestion
	sel      int
	cache    *app.Cache
	pending  []core.Fix
	admin    bool
	sysFree  string
	sampler  *sampler
	snap     snapshot
	quitting bool
}

var prog *tea.Program

type (
	progressMsg string
	resultMsg   struct{ r *core.Result }
	printMsg    string
	fixesMsg    struct {
		fixes []core.Fix
		yes   bool
		err   error
		title string
		bytes int64
	}
	appliedMsg struct{}
)

// Run starts the interactive shell. If first is non-empty it runs as the first command.
func Run(version, first string) error {
	ti := textinput.New()
	ti.Prompt = "› "
	ti.PromptStyle = sAccent
	ti.Placeholder = "ask anything (\"why is my pc slow\", \"وفر مساحة\") or type / for tools"
	ti.PlaceholderStyle = sFaint
	ti.Focus()
	ti.CharLimit = 500
	sp := spinner.New()
	sp.Spinner = spinner.Dot
	sp.Style = sAccent
	m := &Model{ti: ti, spin: sp, cache: app.NewCache(), admin: sys.IsAdmin(), width: 100, hIdx: -1}
	m.sysFree = systemDriveFree()
	prog = tea.NewProgram(m)
	go func() {
		prog.Println(banner(version, m.admin, m.sysFree))
		if first != "" {
			prog.Send(submitMsg(first))
		}
	}()
	_, err := prog.Run()
	return err
}

type submitMsg string

func systemDriveFree() string {
	root := sys.Known().SystemDrive + `\`
	if !sys.IsWindows {
		root = "/"
	}
	u, err := disk.Usage(root)
	if err != nil || u.Total == 0 {
		return ""
	}
	return fmt.Sprintf("%s %s free", strings.TrimRight(root, `\`), sys.HumanBytes(int64(u.Free)))
}

func banner(version string, admin bool, free string) string {
	logo := sAccent.Bold(true).Render("◆ WinSight") + sDim.Render("  "+version)
	sub := sDim.Render("Windows doctor · space · speed · health · repair · secrets · agent brief")
	adm := sYellow.Render("not elevated — some checks and fixes need admin (/admin to relaunch)")
	if admin {
		adm = sGreen.Render("running as administrator")
	}
	box := lipgloss.NewStyle().Border(lipgloss.RoundedBorder()).BorderForeground(cAccent).Padding(0, 2).Render(
		logo + "\n" + sub + "\n\n" + adm + "\n" + sDim.Render(free) + "\n\n" +
			sFaint.Render("Try: ") + sCode.Render("doctor") + sFaint.Render("  ·  ") + sCode.Render("junk") + sFaint.Render("  ·  ") +
			sCode.Render("startup") + sFaint.Render("  ·  ") + sCode.Render("missing") + sFaint.Render("  ·  ") + sCode.Render("brief") + sFaint.Render("  ·  ") + sCode.Render("/help"))
	return box
}

// Init implements tea.Model.
func (m *Model) Init() tea.Cmd { return tea.Batch(textinput.Blink, m.spin.Tick) }

// Update implements tea.Model.
func (m *Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width, m.height = msg.Width, msg.Height
		m.ti.Width = msg.Width - 8
		return m, nil
	case spinner.TickMsg:
		var cmd tea.Cmd
		m.spin, cmd = m.spin.Update(msg)
		return m, cmd
	case progressMsg:
		m.status = string(msg)
		return m, nil
	case printMsg:
		return m, tea.Println(string(msg))
	case submitMsg:
		return m, m.submit(string(msg))
	case resultMsg:
		m.mode = modeInput
		m.cancel = nil
		if d, ok := msg.r.Data.(brief.Doc); ok {
			m.cache.PutAll(d.Results)
		}
		return m, tea.Println(Result(msg.r, Options{Width: m.width - 2}) + nextHint(msg.r))
	case fixesMsg:
		return m.onFixes(msg)
	case appliedMsg:
		m.mode = modeInput
		m.cancel = nil
		m.sysFree = systemDriveFree()
		return m, nil
	case monTickMsg:
		if m.mode != modeMonitor {
			return m, nil
		}
		return m, tea.Batch(m.monSample(), monTick())
	case monSnapMsg:
		m.snap = snapshot(msg)
		return m, nil
	case tea.KeyMsg:
		return m.onKey(msg)
	}
	return m, nil
}

func nextHint(r *core.Result) string {
	for _, f := range r.Findings {
		for _, fx := range f.Fixes {
			if fx.Risk == core.Safe {
				return "\n" + sFaint.Render("  next: ") + sCode.Render("fix "+fx.ID) + sFaint.Render(" to preview, or ") + sCode.Render("fix "+r.Tool+".*") + sFaint.Render(" for all of them")
			}
		}
	}
	return ""
}

func (m *Model) onKey(k tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch m.mode {
	case modeMonitor:
		switch k.String() {
		case "q", "esc", "ctrl+c":
			m.mode = modeInput
			return m, tea.ExitAltScreen
		}
		return m, nil
	case modeRunning:
		switch k.String() {
		case "esc", "ctrl+c":
			if m.cancel != nil {
				m.cancel()
			}
			m.status = "cancelling…"
		}
		return m, nil
	case modeConfirm:
		switch strings.ToLower(k.String()) {
		case "y", "enter":
			fixes := m.pending
			m.pending = nil
			return m, m.apply(fixes)
		case "n", "esc", "ctrl+c":
			m.pending = nil
			m.mode = modeInput
			return m, tea.Println(sDim.Render("  cancelled — nothing was changed"))
		}
		return m, nil
	}
	switch k.String() {
	case "ctrl+c":
		if m.ti.Value() != "" {
			m.ti.SetValue("")
			m.sugg = nil
			return m, nil
		}
		m.quitting = true
		return m, tea.Quit
	case "ctrl+d":
		m.quitting = true
		return m, tea.Quit
	case "ctrl+l":
		return m, tea.ClearScreen
	case "esc":
		m.sugg = nil
		return m, nil
	case "tab":
		if len(m.sugg) > 0 {
			m.ti.SetValue("/" + m.sugg[m.sel].name + " ")
			m.ti.CursorEnd()
			m.sugg = nil
		}
		return m, nil
	case "up":
		if len(m.sugg) > 0 {
			m.sel = (m.sel - 1 + len(m.sugg)) % len(m.sugg)
			return m, nil
		}
		if len(m.history) > 0 {
			if m.hIdx < 0 {
				m.hIdx = len(m.history)
			}
			if m.hIdx > 0 {
				m.hIdx--
			}
			m.ti.SetValue(m.history[m.hIdx])
			m.ti.CursorEnd()
		}
		return m, nil
	case "down":
		if len(m.sugg) > 0 {
			m.sel = (m.sel + 1) % len(m.sugg)
			return m, nil
		}
		if m.hIdx >= 0 && m.hIdx < len(m.history)-1 {
			m.hIdx++
			m.ti.SetValue(m.history[m.hIdx])
			m.ti.CursorEnd()
		} else {
			m.hIdx = -1
			m.ti.SetValue("")
		}
		return m, nil
	case "enter":
		line := strings.TrimSpace(m.ti.Value())
		if len(m.sugg) > 0 && !strings.Contains(line, " ") && strings.HasPrefix(line, "/") && line != "/"+m.sugg[m.sel].name {
			line = "/" + m.sugg[m.sel].name
		}
		m.ti.SetValue("")
		m.sugg = nil
		m.hIdx = -1
		if line == "" {
			return m, nil
		}
		if len(m.history) == 0 || m.history[len(m.history)-1] != line {
			m.history = append(m.history, line)
		}
		return m, tea.Sequence(tea.Println(sFaint.Render("› ")+sBold.Render(line)), m.submit(line))
	}
	var cmd tea.Cmd
	m.ti, cmd = m.ti.Update(k)
	m.refreshSuggestions()
	return m, cmd
}

func (m *Model) refreshSuggestions() {
	v := m.ti.Value()
	m.sugg = nil
	m.sel = 0
	if !strings.HasPrefix(v, "/") || strings.Contains(v, " ") {
		return
	}
	q := strings.ToLower(strings.TrimPrefix(v, "/"))
	var pre, sub []suggestion
	add := func(name, desc string) {
		switch {
		case strings.HasPrefix(name, q):
			pre = append(pre, suggestion{name, desc})
		case q != "" && strings.Contains(name, q):
			sub = append(sub, suggestion{name, desc})
		}
	}
	for _, t := range core.All() {
		add(t.Name, t.Short)
	}
	for _, c := range Commands {
		add(c.Name, c.Desc)
	}
	sort.SliceStable(pre, func(i, j int) bool { return len(pre[i].name) < len(pre[j].name) && q != "" })
	m.sugg = append(pre, sub...)
	if len(m.sugg) > 8 {
		m.sugg = m.sugg[:8]
	}
}

// submit dispatches one line of input.
func (m *Model) submit(line string) tea.Cmd {
	argv := core.SplitCommandLine(strings.TrimPrefix(line, "/"))
	if len(argv) == 0 {
		return nil
	}
	name := strings.ToLower(argv[0])
	pos, flags := core.ParseArgs(argv[1:])
	switch name {
	case "exit", "quit", "q":
		m.quitting = true
		return tea.Quit
	case "help", "?", "h":
		return tea.Println(Help(m.width))
	case "clear", "cls":
		return tea.ClearScreen
	case "admin", "elevate":
		if err := sys.Elevate(nil); err != nil {
			return tea.Println(sRed.Render("  could not relaunch elevated: " + err.Error()))
		}
		return tea.Println(sGreen.Render("  An elevated WinSight window is opening (approve the UAC prompt)."))
	case "monitor", "mon", "live":
		m.mode = modeMonitor
		if m.sampler == nil {
			m.sampler = newSampler()
		}
		m.snap = snapshot{}
		return tea.Batch(tea.EnterAltScreen, m.monSample(), monTick())
	case "journal", "history":
		return tea.Println(journalView(m.width))
	case "undo":
		if len(pos) == 0 {
			return tea.Println(sYellow.Render("  usage: undo <journal-id>  (see /journal)"))
		}
		return m.background("undo", func(ctx context.Context) tea.Msg {
			o := fix.Undo(ctx, pos[0])
			prog.Println(Outcome(o))
			return appliedMsg{}
		})
	case "fix", "apply":
		if len(pos) == 0 {
			return tea.Println(sYellow.Render("  usage: fix <fix-id or pattern> [--yes]   e.g. fix junk.user-temp.clean · fix \"tweaks.*\""))
		}
		yes := flags["yes"] == "true" || flags["y"] == "true"
		return m.background("locating fixes", func(ctx context.Context) tea.Msg {
			fixes, err := m.cache.Resolve(ctx, pos, flags, func(s string) { prog.Send(progressMsg(s)) })
			return fixesMsg{fixes: fixes, yes: yes, err: err, title: strings.Join(pos, " ")}
		})
	case "clean":
		yes := flags["yes"] == "true"
		return m.background("measuring junk", func(ctx context.Context) tea.Msg {
			fixes, total := m.cache.CleanFixes(ctx, func(s string) { prog.Send(progressMsg(s)) })
			return fixesMsg{fixes: fixes, yes: yes, title: "clean", bytes: total}
		})
	}
	var routed tea.Cmd
	t, ok := core.Get(name)
	if !ok {
		t2, score := core.Match(line)
		if t2 == nil || score < 2 {
			return tea.Println(sYellow.Render("  I don't know that one. ") + sDim.Render("Type /help to see all tools, or try: doctor"))
		}
		t = t2
		pos, flags = nil, map[string]string{}
		routed = tea.Println(sFaint.Render("  → ") + sCode.Render(t.Name) + sFaint.Render("  "+t.Short))
	}
	run := m.background(t.Name, func(ctx context.Context) tea.Msg {
		r := m.cache.Run(ctx, t, pos, flags, func(s string) { prog.Send(progressMsg(s)) })
		if flags["json"] == "true" {
			prog.Println(toJSON(r))
		}
		return resultMsg{r}
	})
	if routed != nil {
		return tea.Sequence(routed, run)
	}
	return run
}

func toJSON(v any) string {
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err.Error()
	}
	return string(b)
}

// background runs fn off the UI thread with a cancellable context.
func (m *Model) background(label string, fn func(ctx context.Context) tea.Msg) tea.Cmd {
	ctx, cancel := context.WithCancel(context.Background())
	m.cancel = cancel
	m.mode = modeRunning
	m.running = label
	m.status = ""
	m.started = time.Now()
	return tea.Batch(m.spin.Tick, func() tea.Msg { return fn(ctx) })
}

func (m *Model) onFixes(msg fixesMsg) (tea.Model, tea.Cmd) {
	m.mode = modeInput
	m.cancel = nil
	if msg.err != nil {
		return m, tea.Println(sYellow.Render("  " + msg.err.Error()))
	}
	if len(msg.fixes) == 0 {
		return m, tea.Println(sGreen.Render("  Nothing to do — already clean."))
	}
	var b strings.Builder
	head := fmt.Sprintf("  %d fix(es) for %s", len(msg.fixes), msg.title)
	if msg.bytes > 0 {
		head += " — about " + sys.HumanBytes(msg.bytes)
	}
	b.WriteString(sBold.Render(head) + "\n")
	risky := false
	for _, fx := range msg.fixes {
		if len(msg.fixes) <= 6 {
			b.WriteString(Outcome(fix.Apply(context.Background(), &fx, true, nil)))
		} else {
			b.WriteString("  " + riskStyle(fx.Risk).Render("■ ") + sCode.Render(fx.ID) + "  " + sDim.Render(fx.Title) + "\n")
		}
		if fx.Risk == core.Risky {
			risky = true
		}
	}
	runnable, needAdmin := app.SplitByAdmin(msg.fixes, m.admin)
	if len(needAdmin) > 0 {
		b.WriteString(sYellow.Render(fmt.Sprintf("  %d of them need administrator rights and will be skipped here. Use /admin to relaunch elevated.", len(needAdmin))) + "\n")
	}
	if len(runnable) == 0 {
		return m, tea.Println(b.String())
	}
	if msg.yes && !risky {
		return m, tea.Sequence(tea.Println(b.String()), m.apply(runnable))
	}
	if risky {
		b.WriteString(sRed.Render("  ⚠ includes RISKY fixes that touch personal files — read the list above carefully.") + "\n")
	}
	m.pending = runnable
	m.mode = modeConfirm
	return m, tea.Println(b.String())
}

func (m *Model) apply(fixes []core.Fix) tea.Cmd {
	return m.background("applying fixes", func(ctx context.Context) tea.Msg {
		var freed int64
		ok := 0
		for i := range fixes {
			prog.Send(progressMsg(fmt.Sprintf("[%d/%d] %s", i+1, len(fixes), fixes[i].Title)))
			o := fix.Apply(ctx, &fixes[i], false, func(s string) { prog.Send(progressMsg(s)) })
			prog.Println(Outcome(o))
			freed += o.Freed
			if o.OK {
				ok++
			}
			if ctx.Err() != nil {
				break
			}
		}
		summary := fmt.Sprintf("  %d/%d applied", ok, len(fixes))
		if freed > 0 {
			summary += " · freed " + sys.HumanBytes(freed)
		}
		prog.Println(sGreen.Render(summary) + sFaint.Render("  · /journal to review, undo <id> to revert"))
		return appliedMsg{}
	})
}

func journalView(width int) string {
	entries, err := fix.Journal()
	if err != nil {
		return sRed.Render("  " + err.Error())
	}
	if len(entries) == 0 {
		return sDim.Render("  Nothing changed yet.")
	}
	t := core.Table{Title: "Journal (newest first)", Headers: []string{"ID", "When", "Fix", "Result", "Undo"}}
	for i := len(entries) - 1; i >= 0 && len(t.Rows) < 25; i-- {
		e := entries[i]
		res := "ok"
		if !e.OK {
			res = "failed"
		}
		if e.Freed > 0 {
			res += " · " + sys.HumanBytes(e.Freed)
		}
		undo := ""
		switch {
		case e.Undone:
			undo = "undone"
		case e.Reversible():
			undo = "undo " + e.ID
		}
		t.Rows = append(t.Rows, []string{e.ID, e.Time.Format("01-02 15:04"), e.FixID, res, undo})
	}
	return indent(Table(t, width-4, false), 2)
}

// View implements tea.Model.
func (m *Model) View() string {
	if m.quitting {
		return ""
	}
	if m.mode == modeMonitor {
		return m.monitorView()
	}
	var b strings.Builder
	switch m.mode {
	case modeRunning:
		el := time.Since(m.started).Round(time.Second)
		status := m.status
		if status == "" {
			status = "working"
		}
		b.WriteString(" " + m.spin.View() + sAccent.Render(m.running) + sDim.Render(" — "+sys.Truncate(status, m.width-30)) +
			sFaint.Render(fmt.Sprintf("  (%s · esc to cancel)", el)) + "\n")
	case modeConfirm:
		b.WriteString(sBold.Render(fmt.Sprintf(" Apply %d fix(es)? ", len(m.pending))) + sGreen.Render("[y]es") + sDim.Render(" / ") + sRed.Render("[n]o") + "\n")
		return b.String()
	}
	w := m.width - 2
	if w < 20 {
		w = 20
	}
	box := lipgloss.NewStyle().Border(lipgloss.RoundedBorder()).BorderForeground(cFaint).Width(w - 2).Render(m.ti.View())
	b.WriteString(box + "\n")
	for i, s := range m.sugg {
		line := fmt.Sprintf("  /%-12s %s", s.name, sys.Truncate(s.desc, w-18))
		if i == m.sel {
			b.WriteString(sAccent.Render("›") + sBold.Render(line[1:]) + "\n")
		} else {
			b.WriteString(sDim.Render(line) + "\n")
		}
	}
	left := sFaint.Render("  / tools · tab complete · ↑↓ history · ctrl+c quit")
	adm := sYellow.Render("not admin")
	if m.admin {
		adm = sGreen.Render("admin ✓")
	}
	right := adm + sFaint.Render(" · "+m.sysFree)
	b.WriteString(left + strings.Repeat(" ", max0(w-lipgloss.Width(left)-lipgloss.Width(right))) + right)
	return b.String()
}

// IsTerminal reports whether stdout is interactive.
func IsTerminal() bool {
	fi, err := os.Stdout.Stat()
	return err == nil && fi.Mode()&os.ModeCharDevice != 0
}
