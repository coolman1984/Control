package ui

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/shirou/gopsutil/v4/cpu"
	"github.com/shirou/gopsutil/v4/disk"
	"github.com/shirou/gopsutil/v4/mem"
	"github.com/shirou/gopsutil/v4/net"
	"github.com/shirou/gopsutil/v4/process"

	"github.com/coolman1984/performance/internal/sys"
)

type procEntry struct {
	p    *process.Process
	name string
}

type appUsage struct {
	name  string
	count int
	cpu   float64
	rss   uint64
}

// sampler keeps state between monitor ticks (deltas need a previous value).
type sampler struct {
	procs    map[int32]*procEntry
	lastDisk map[string]disk.IOCountersStat
	lastNet  []net.IOCountersStat
	lastAt   time.Time
	cpuHist  []float64
	ramHist  []float64
	ncpu     int
}

func newSampler() *sampler {
	n, _ := cpu.Counts(true)
	if n == 0 {
		n = 1
	}
	_, _ = cpu.Percent(0, false) // prime the delta
	_, _ = cpu.Percent(0, true)
	return &sampler{procs: map[int32]*procEntry{}, ncpu: n}
}

type snapshot struct {
	at               time.Time
	cpu              float64
	cores            []float64
	mem              *mem.VirtualMemoryStat
	readBps, writeBs float64
	rxBps, txBps     float64
	apps             []appUsage
}

type monTickMsg time.Time
type monSnapMsg snapshot

func (s *sampler) sample(ctx context.Context) snapshot {
	now := time.Now()
	snap := snapshot{at: now}
	if v, err := cpu.PercentWithContext(ctx, 0, false); err == nil && len(v) > 0 {
		snap.cpu = v[0]
	}
	snap.cores, _ = cpu.PercentWithContext(ctx, 0, true)
	snap.mem, _ = mem.VirtualMemoryWithContext(ctx)
	dt := now.Sub(s.lastAt).Seconds()
	if io, err := disk.IOCountersWithContext(ctx); err == nil {
		if s.lastDisk != nil && dt > 0 {
			for k, v := range io {
				if p, ok := s.lastDisk[k]; ok {
					snap.readBps += float64(v.ReadBytes-p.ReadBytes) / dt
					snap.writeBs += float64(v.WriteBytes-p.WriteBytes) / dt
				}
			}
		}
		s.lastDisk = io
	}
	if nc, err := net.IOCountersWithContext(ctx, false); err == nil && len(nc) > 0 {
		if len(s.lastNet) > 0 && dt > 0 {
			snap.rxBps = float64(nc[0].BytesRecv-s.lastNet[0].BytesRecv) / dt
			snap.txBps = float64(nc[0].BytesSent-s.lastNet[0].BytesSent) / dt
		}
		s.lastNet = nc
	}
	s.lastAt = now

	pids, _ := process.PidsWithContext(ctx)
	alive := map[int32]bool{}
	groups := map[string]*appUsage{}
	for _, pid := range pids {
		alive[pid] = true
		e := s.procs[pid]
		if e == nil {
			p, err := process.NewProcessWithContext(ctx, pid)
			if err != nil {
				continue
			}
			name, _ := p.NameWithContext(ctx)
			if name == "" {
				continue
			}
			e = &procEntry{p: p, name: strings.TrimSuffix(name, ".exe")}
			s.procs[pid] = e
		}
		c, _ := e.p.PercentWithContext(ctx, 0)
		var rss uint64
		if mi, err := e.p.MemoryInfoWithContext(ctx); err == nil {
			rss = mi.RSS
		}
		key := strings.ToLower(e.name)
		g := groups[key]
		if g == nil {
			g = &appUsage{name: e.name}
			groups[key] = g
		}
		g.count++
		g.cpu += c / float64(s.ncpu)
		g.rss += rss
	}
	for pid := range s.procs {
		if !alive[pid] {
			delete(s.procs, pid)
		}
	}
	for _, g := range groups {
		if g.name == "System Idle Process" || g.name == "Idle" {
			continue
		}
		snap.apps = append(snap.apps, *g)
	}
	s.cpuHist = pushHist(s.cpuHist, snap.cpu)
	if snap.mem != nil {
		s.ramHist = pushHist(s.ramHist, snap.mem.UsedPercent)
	}
	return snap
}

func pushHist(h []float64, v float64) []float64 {
	h = append(h, v)
	if len(h) > 60 {
		h = h[len(h)-60:]
	}
	return h
}

func monTick() tea.Cmd {
	return tea.Tick(1500*time.Millisecond, func(t time.Time) tea.Msg { return monTickMsg(t) })
}

func (m *Model) monSample() tea.Cmd {
	s := m.sampler
	return func() tea.Msg { return monSnapMsg(s.sample(context.Background())) }
}

var sparks = []rune("▁▂▃▄▅▆▇█")

func sparkline(h []float64, width int) string {
	if len(h) > width {
		h = h[len(h)-width:]
	}
	var b strings.Builder
	for _, v := range h {
		i := int(v / 100 * float64(len(sparks)-1))
		if i < 0 {
			i = 0
		}
		if i >= len(sparks) {
			i = len(sparks) - 1
		}
		b.WriteRune(sparks[i])
	}
	return b.String()
}

func meter(pct float64, width int) string {
	n := int(pct / 100 * float64(width))
	if n > width {
		n = width
	}
	col := cGreen
	switch {
	case pct >= 85:
		col = cRed
	case pct >= 60:
		col = cYellow
	}
	return lipgloss.NewStyle().Foreground(col).Render(strings.Repeat("█", n)) + sFaint.Render(strings.Repeat("░", width-n))
}

func rate(bps float64) string { return sys.HumanBytes(int64(bps)) + "/s" }

func (m *Model) monitorView() string {
	w := m.width
	if w <= 0 {
		w = 100
	}
	s := m.snap
	var b strings.Builder
	title := sAccent.Render("◆ WinSight monitor")
	right := sFaint.Render(time.Now().Format("15:04:05") + "  ·  q/esc to leave")
	b.WriteString(title + strings.Repeat(" ", max0(w-lipgloss.Width(title)-lipgloss.Width(right))) + right + "\n\n")
	if s.at.IsZero() {
		return b.String() + sDim.Render("  sampling…")
	}
	bw := w/3 - 4
	if bw < 10 {
		bw = 10
	}
	b.WriteString(fmt.Sprintf("  %s %s %5.1f%%   %s\n", sBold.Render("CPU"), meter(s.cpu, bw), s.cpu, sAccent.Render(sparkline(m.sampler.cpuHist, w-bw-22))))
	if s.mem != nil {
		b.WriteString(fmt.Sprintf("  %s %s %5.1f%%   %s  %s\n", sBold.Render("RAM"), meter(s.mem.UsedPercent, bw), s.mem.UsedPercent,
			sDim.Render(fmt.Sprintf("%s / %s", sys.HumanBytes(int64(s.mem.Used)), sys.HumanBytes(int64(s.mem.Total)))), sBlueSpark(sparkline(m.sampler.ramHist, w-bw-45))))
	}
	b.WriteString(fmt.Sprintf("  %s read %-12s write %-12s   %s ↓ %-12s ↑ %s\n\n", sBold.Render("Disk"), rate(s.readBps), rate(s.writeBs), sBold.Render("Net"), rate(s.rxBps), rate(s.txBps)))
	if len(s.cores) > 0 {
		var cs []string
		for _, c := range s.cores {
			i := int(c / 100 * float64(len(sparks)-1))
			if i < 0 {
				i = 0
			}
			cs = append(cs, string(sparks[i]))
		}
		b.WriteString("  " + sDim.Render("cores ") + sAccent.Render(strings.Join(cs, "")) + "\n\n")
	}
	apps := append([]appUsage(nil), s.apps...)
	sort.Slice(apps, func(i, j int) bool { return apps[i].cpu > apps[j].cpu })
	rows := m.height - 14
	if rows < 5 {
		rows = 5
	}
	if rows > 15 {
		rows = 15
	}
	colW := w/2 - 4
	left := []string{sDim.Render(fmt.Sprintf("%-*s %6s %9s", colW-18, "Top CPU", "CPU", "RAM"))}
	for i := 0; i < rows && i < len(apps); i++ {
		a := apps[i]
		left = append(left, fmt.Sprintf("%-*s %5.1f%% %9s", colW-18, sys.Truncate(fmt.Sprintf("%s ×%d", a.name, a.count), colW-18), a.cpu, sys.HumanBytes(int64(a.rss))))
	}
	sort.Slice(apps, func(i, j int) bool { return apps[i].rss > apps[j].rss })
	right2 := []string{sDim.Render(fmt.Sprintf("%-*s %9s %6s", colW-18, "Top RAM", "RAM", "CPU"))}
	for i := 0; i < rows && i < len(apps); i++ {
		a := apps[i]
		right2 = append(right2, fmt.Sprintf("%-*s %9s %5.1f%%", colW-18, sys.Truncate(fmt.Sprintf("%s ×%d", a.name, a.count), colW-18), sys.HumanBytes(int64(a.rss)), a.cpu))
	}
	b.WriteString(lipgloss.JoinHorizontal(lipgloss.Top, indent(strings.Join(left, "\n"), 2), "    ", strings.Join(right2, "\n")))
	return b.String()
}

func sBlueSpark(s string) string { return lipgloss.NewStyle().Foreground(cBlue).Render(s) }
