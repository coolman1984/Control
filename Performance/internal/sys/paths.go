package sys

import (
	"os"
	"path/filepath"
	"strings"
)

// Folders holds well-known Windows locations resolved from the environment.
type Folders struct {
	User, Local, Roaming, Temp, Windows, ProgramData, SystemDrive, ProgramFiles, ProgramFilesX86, Downloads, Desktop string
}

// Known resolves the well-known folders for the current user.
func Known() Folders {
	home, _ := os.UserHomeDir()
	f := Folders{
		User:            env("USERPROFILE", home),
		Local:           env("LOCALAPPDATA", filepath.Join(home, "AppData", "Local")),
		Roaming:         env("APPDATA", filepath.Join(home, "AppData", "Roaming")),
		Temp:            env("TEMP", os.TempDir()),
		Windows:         env("SystemRoot", `C:\Windows`),
		ProgramData:     env("ProgramData", `C:\ProgramData`),
		SystemDrive:     env("SystemDrive", "C:"),
		ProgramFiles:    env("ProgramFiles", `C:\Program Files`),
		ProgramFilesX86: env("ProgramFiles(x86)", `C:\Program Files (x86)`),
	}
	f.Downloads = filepath.Join(f.User, "Downloads")
	f.Desktop = filepath.Join(f.User, "Desktop")
	return f
}

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

// ExpandKnown resolves %VAR% and the WinSight tokens {local}, {roaming},
// {user}, {windows}, {programdata}, {temp}.
func ExpandKnown(p string) string {
	k := Known()
	r := strings.NewReplacer("{local}", k.Local, "{roaming}", k.Roaming, "{user}", k.User,
		"{windows}", k.Windows, "{programdata}", k.ProgramData, "{temp}", k.Temp,
		"{sysdrive}", k.SystemDrive, "{programfiles}", k.ProgramFiles)
	return filepath.Clean(Expand(r.Replace(p)))
}

// Glob expands a pattern that may contain * in any segment; it returns
// existing directories/files only.
func Glob(pattern string) []string {
	if !strings.ContainsAny(pattern, "*?[") {
		if Exists(pattern) {
			return []string{pattern}
		}
		return nil
	}
	m, _ := filepath.Glob(pattern)
	return m
}
