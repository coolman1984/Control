// WinSight: a Claude-Code-style terminal toolkit that finds and fixes what
// makes Windows full, slow, broken or insecure — for humans and AI agents.
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/signal"
	"strings"

	"github.com/charmbracelet/lipgloss"

	"github.com/coolman1984/performance/internal/app"
	"github.com/coolman1984/performance/internal/brief"
	"github.com/coolman1984/performance/internal/core"
	"github.com/coolman1984/performance/internal/fix"
	"github.com/coolman1984/performance/internal/mcp"
	"github.com/coolman1984/performance/internal/sys"
	_ "github.com/coolman1984/performance/internal/tools"
	"github.com/coolman1984/performance/internal/ui"
)

var version = "0.1.0"

func main() {
	brief.Version = version
	enableVT()
	// Asking the terminal for its background colour can hang on consoles that
	// never answer; assume dark unless told otherwise.
	lipgloss.SetHasDarkBackground(os.Getenv("WINSIGHT_LIGHT") == "")
	os.Exit(run(os.Args[1:]))
}

func run(argv []string) int {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	if len(argv) == 0 {
		if !ui.IsTerminal() {
			fmt.Print(ui.Help(100))
			return 0
		}
		return exitErr(ui.Run(version, ""))
	}
	name := strings.ToLower(strings.TrimPrefix(argv[0], "/"))
	pos, flags := core.ParseArgs(argv[1:])
	if flags["elevate"] == "true" && !sys.IsAdmin() {
		var args []string
		for _, a := range argv {
			if a != "--elevate" {
				args = append(args, a)
			}
		}
		if err := sys.Elevate(append(args, "--pause")); err != nil {
			return exitErr(err)
		}
		fmt.Println("An elevated WinSight window was opened (approve the UAC prompt).")
		return 0
	}
	if flags["pause"] == "true" {
		defer func() {
			fmt.Print("\nPress Enter to close…")
			_, _ = bufio.NewReader(os.Stdin).ReadString('\n')
		}()
	}
	width := termWidth()
	switch name {
	case "-h", "--help", "help", "list", "tools":
		fmt.Print(ui.Help(width))
		return 0
	case "-v", "--version", "version":
		fmt.Println("winsight", version)
		return 0
	case "mcp", "serve":
		return exitErr(mcp.Serve(ctx, version, os.Stdin, os.Stdout))
	case "shell", "repl", "i":
		return exitErr(ui.Run(version, ""))
	case "monitor", "mon", "live":
		return exitErr(ui.Run(version, "monitor"))
	case "journal":
		entries, err := fix.Journal()
		if err != nil {
			return exitErr(err)
		}
		if flags["json"] == "true" {
			printJSON(entries)
			return 0
		}
		for _, e := range entries {
			state := "ok"
			if !e.OK {
				state = "failed: " + e.Error
			}
			if e.Undone {
				state += " (undone)"
			}
			fmt.Printf("%s  %-45s %s\n", e.ID, e.FixID, state)
		}
		return 0
	case "undo":
		if len(pos) == 0 {
			fmt.Fprintln(os.Stderr, "usage: winsight undo <journal-id>")
			return 2
		}
		o := fix.Undo(ctx, pos[0])
		return printOutcomes([]fix.Outcome{o}, flags)
	case "fix", "apply", "clean":
		cache := app.NewCache()
		var fixes []core.Fix
		var err error
		if name == "clean" {
			fixes, _ = cache.CleanFixes(ctx, progress(flags))
		} else {
			if len(pos) == 0 {
				fmt.Fprintln(os.Stderr, "usage: winsight fix <fix-id|pattern>... [--yes]")
				return 2
			}
			fixes, err = cache.Resolve(ctx, pos, flags, progress(flags))
			if err != nil {
				return exitErr(err)
			}
		}
		apply := flags["yes"] == "true" || flags["y"] == "true"
		var outs []fix.Outcome
		for i := range fixes {
			outs = append(outs, fix.Apply(ctx, &fixes[i], !apply, progress(flags)))
		}
		code := printOutcomes(outs, flags)
		if !apply && flags["json"] != "true" {
			fmt.Println("\nThis was a preview. Add --yes to apply.")
		}
		return code
	}
	t, ok := core.Get(name)
	if !ok {
		if t2, score := core.Match(strings.Join(argv, " ")); t2 != nil && score >= 2 {
			t, pos, flags = t2, nil, map[string]string{}
		} else {
			fmt.Fprintf(os.Stderr, "unknown tool %q — run `winsight help`\n", name)
			return 2
		}
	}
	r := core.Execute(t, core.NewCtx(ctx, pos, flags, progress(flags)))
	switch {
	case flags["json"] == "true":
		printJSON(r)
	case flags["md"] == "true":
		if d, ok := r.Data.(brief.Doc); ok {
			fmt.Print(brief.Markdown(d))
		} else {
			fmt.Print(brief.ResultMarkdown(r))
		}
	default:
		fmt.Print(ui.Result(r, ui.Options{Width: width, All: flags["all"] == "true"}))
	}
	return 0
}

func progress(flags map[string]string) func(string) {
	if flags["json"] == "true" || flags["quiet"] == "true" || !ui.IsTerminal() {
		return nil
	}
	return func(s string) { fmt.Fprintf(os.Stderr, "\r\033[K  … %s", sys.Truncate(s, 90)) }
}

func printOutcomes(outs []fix.Outcome, flags map[string]string) int {
	if ui.IsTerminal() {
		fmt.Fprint(os.Stderr, "\r\033[K")
	}
	code := 0
	for _, o := range outs {
		if !o.OK {
			code = 1
		}
	}
	if flags["json"] == "true" {
		printJSON(outs)
		return code
	}
	var freed int64
	for i, o := range outs {
		freed += o.Freed
		if i == 12 && o.DryRun {
			fmt.Printf("… and %d more (use --json to see all)\n", len(outs)-12)
		}
		if i >= 12 && o.DryRun {
			continue
		}
		fmt.Print(ui.Outcome(o))
	}
	if freed > 0 {
		fmt.Printf("\nFreed %s in total.\n", sys.HumanBytes(freed))
	}
	return code
}

func printJSON(v any) {
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	_ = enc.Encode(v)
}

func exitErr(err error) int {
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		return 1
	}
	return 0
}
