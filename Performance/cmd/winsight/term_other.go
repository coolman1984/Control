//go:build !windows

package main

import (
	"os"

	"golang.org/x/sys/unix"
)

func enableVT() {}

func termWidth() int {
	if ws, err := unix.IoctlGetWinsize(int(os.Stdout.Fd()), unix.TIOCGWINSZ); err == nil && ws.Col > 20 {
		return int(ws.Col) - 1
	}
	return 100
}
