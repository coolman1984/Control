//go:build !windows

package sys

import (
	"io/fs"
	"os"
	"os/exec"
)

func hideWindow(*exec.Cmd) {}

// IsAdmin reports whether the process runs as root.
func IsAdmin() bool { return os.Geteuid() == 0 }

// IsCloudPlaceholder is always false outside Windows.
func IsCloudPlaceholder(fs.FileInfo) bool { return false }

// Elevate is only supported on Windows.
func Elevate([]string) error { return ErrNotWindows }
