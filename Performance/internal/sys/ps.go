package sys

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"unicode/utf16"
)

// ErrNotWindows is returned by Windows-only probes on other systems.
var ErrNotWindows = errors.New("this check needs Windows")

// IsWindows is true when running on Windows.
const IsWindows = runtime.GOOS == "windows"

const psPrelude = "$ErrorActionPreference='SilentlyContinue';$ProgressPreference='SilentlyContinue';" +
	"try{[Console]::OutputEncoding=[Text.Encoding]::UTF8}catch{};"

func powershellExe() string {
	if p := os.Getenv("WINSIGHT_POWERSHELL"); p != "" {
		return p
	}
	if IsWindows {
		p := filepath.Join(os.Getenv("SystemRoot"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
		if Exists(p) {
			return p
		}
		return "powershell.exe"
	}
	return ""
}

// PS runs a PowerShell script and returns its stdout.
func PS(ctx context.Context, script string) (string, error) {
	exe := powershellExe()
	if exe == "" {
		return "", ErrNotWindows
	}
	full := psPrelude + "\n" + script
	args := []string{"-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"}
	var tmp string
	if len(full) < 7000 {
		u := utf16.Encode([]rune(full))
		b := make([]byte, len(u)*2)
		for i, c := range u {
			b[2*i], b[2*i+1] = byte(c), byte(c>>8)
		}
		args = append(args, "-EncodedCommand", base64.StdEncoding.EncodeToString(b))
	} else {
		f, err := os.CreateTemp("", "winsight-*.ps1")
		if err != nil {
			return "", err
		}
		// UTF-8 BOM so Windows PowerShell 5.1 reads non-ASCII correctly.
		_, _ = f.Write([]byte{0xEF, 0xBB, 0xBF})
		_, _ = f.WriteString(full)
		f.Close()
		tmp = f.Name()
		defer os.Remove(tmp)
		args = append(args, "-File", tmp)
	}
	cmd := exec.CommandContext(ctx, exe, args...)
	hideWindow(cmd)
	var out, errb bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &errb
	err := cmd.Run()
	if ctx.Err() != nil {
		return "", ctx.Err()
	}
	if err != nil && out.Len() == 0 {
		msg := strings.TrimSpace(errb.String())
		if msg == "" {
			msg = err.Error()
		}
		return "", fmt.Errorf("powershell: %s", Truncate(msg, 400))
	}
	return out.String(), nil
}

// PSJSON runs a script whose final expression is a single object (use a
// hashtable or [pscustomobject]) and decodes it into v.
func PSJSON(ctx context.Context, script string, v any) error {
	wrapped := "$__r = & {\n" + script + "\n}\nConvertTo-Json -InputObject $__r -Depth 8 -Compress"
	out, err := PS(ctx, wrapped)
	if err != nil {
		return err
	}
	i := strings.IndexAny(out, "{[")
	if i < 0 {
		return fmt.Errorf("powershell returned no JSON: %s", Truncate(strings.TrimSpace(out), 200))
	}
	if err := json.Unmarshal([]byte(strings.TrimSpace(out[i:])), v); err != nil {
		return fmt.Errorf("decode powershell JSON: %w", err)
	}
	return nil
}

// Exec runs a program without a console window and returns combined output.
func Exec(ctx context.Context, name string, args ...string) (string, error) {
	cmd := exec.CommandContext(ctx, name, args...)
	hideWindow(cmd)
	out, err := cmd.CombinedOutput()
	return string(out), err
}
