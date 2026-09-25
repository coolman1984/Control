//go:build windows

package sys

import (
	"io/fs"
	"os"
	"os/exec"
	"strings"
	"syscall"

	"golang.org/x/sys/windows"
)

func hideWindow(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000} // CREATE_NO_WINDOW
}

// IsAdmin reports whether the process runs elevated.
func IsAdmin() bool {
	return windows.GetCurrentProcessToken().IsElevated()
}

const (
	attrOffline        = 0x1000
	attrRecallOnOpen   = 0x40000
	attrRecallOnAccess = 0x400000
)

// IsCloudPlaceholder is true for OneDrive-style "online only" files that
// report a size but occupy no local disk.
func IsCloudPlaceholder(fi fs.FileInfo) bool {
	if d, ok := fi.Sys().(*syscall.Win32FileAttributeData); ok {
		return d.FileAttributes&(attrOffline|attrRecallOnOpen|attrRecallOnAccess) != 0
	}
	return false
}

// Elevate relaunches this executable as administrator with args (UAC prompt).
func Elevate(args []string) error {
	exe, err := os.Executable()
	if err != nil {
		return err
	}
	quoted := make([]string, len(args))
	for i, a := range args {
		if strings.ContainsAny(a, " \t\"") {
			a = `"` + strings.ReplaceAll(a, `"`, `\"`) + `"`
		}
		quoted[i] = a
	}
	verb, _ := windows.UTF16PtrFromString("runas")
	file, _ := windows.UTF16PtrFromString(exe)
	params, _ := windows.UTF16PtrFromString(strings.Join(quoted, " "))
	return windows.ShellExecute(0, verb, file, params, nil, windows.SW_SHOWNORMAL)
}
