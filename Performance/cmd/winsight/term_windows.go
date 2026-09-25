//go:build windows

package main

import (
	"os"

	"github.com/muesli/termenv"
	"golang.org/x/sys/windows"
)

func enableVT() {
	_, _ = termenv.EnableVirtualTerminalProcessing(termenv.NewOutput(os.Stdout))
	_ = windows.SetConsoleOutputCP(65001)
}

func termWidth() int {
	var info windows.ConsoleScreenBufferInfo
	if err := windows.GetConsoleScreenBufferInfo(windows.Handle(os.Stdout.Fd()), &info); err == nil {
		if w := int(info.Window.Right - info.Window.Left + 1); w > 20 {
			return w - 1
		}
	}
	return 100
}
