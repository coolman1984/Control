// Package sys wraps the operating system: PowerShell, the filesystem walker,
// well-known Windows folders and small formatting helpers.
package sys

import (
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strings"
	"time"
)

// HumanBytes renders 1536 as "1.5 KB".
func HumanBytes(n int64) string {
	if n < 0 {
		return "-" + HumanBytes(-n)
	}
	const unit = 1024
	if n < unit {
		return fmt.Sprintf("%d B", n)
	}
	div, exp := int64(unit), 0
	for m := n / unit; m >= unit && exp < 5; m /= unit {
		div *= unit
		exp++
	}
	return fmt.Sprintf("%.1f %cB", float64(n)/float64(div), "KMGTPE"[exp])
}

// Pct renders part/total as "42%".
func Pct(part, total float64) string {
	if total <= 0 {
		return "-"
	}
	return fmt.Sprintf("%.0f%%", part*100/total)
}

// Age renders a duration as "3d", "5h", "12m".
func Age(d time.Duration) string {
	switch {
	case d >= 48*time.Hour:
		return fmt.Sprintf("%dd", int(d.Hours()/24))
	case d >= time.Hour:
		return fmt.Sprintf("%dh", int(d.Hours()))
	default:
		return fmt.Sprintf("%dm", int(d.Minutes()))
	}
}

// Truncate shortens s to n display runes with an ellipsis.
func Truncate(s string, n int) string {
	r := []rune(s)
	if n <= 1 || len(r) <= n {
		return s
	}
	return string(r[:n-1]) + "…"
}

var envRe = regexp.MustCompile(`%([A-Za-z0-9_()]+)%`)

// Expand resolves both %VAR% (Windows) and $VAR styles.
func Expand(p string) string {
	p = envRe.ReplaceAllStringFunc(p, func(m string) string {
		if v, ok := os.LookupEnv(strings.Trim(m, "%")); ok {
			return v
		}
		return m
	})
	return p
}

// Exists reports whether a path exists.
func Exists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

// List decodes a JSON value that PowerShell may emit either as a single
// object or as an array (ConvertTo-Json unwraps one-element arrays).
type List[T any] []T

func (l *List[T]) UnmarshalJSON(b []byte) error {
	s := strings.TrimSpace(string(b))
	if s == "null" || s == "" {
		*l = nil
		return nil
	}
	if strings.HasPrefix(s, "[") {
		var many []T
		if err := json.Unmarshal(b, &many); err != nil {
			return err
		}
		*l = many
		return nil
	}
	var one T
	if err := json.Unmarshal(b, &one); err != nil {
		return err
	}
	*l = []T{one}
	return nil
}

// PSQuote makes a Go string safe to embed in a PowerShell script.
func PSQuote(s string) string { return "'" + strings.ReplaceAll(s, "'", "''") + "'" }

// PSArray renders []string as a PowerShell array literal.
func PSArray(items []string) string {
	q := make([]string, len(items))
	for i, s := range items {
		q[i] = PSQuote(s)
	}
	return "@(" + strings.Join(q, ",") + ")"
}
