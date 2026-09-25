package tools

import (
	"encoding/binary"
	"testing"
	"time"
	"unicode/utf16"
)

func TestExePath(t *testing.T) {
	cases := map[string]string{
		`"C:\Program Files\App\app.exe" --min`: `C:\Program Files\App\app.exe`,
		`C:\Tools\x.exe /silent`:               `C:\Tools\x.exe`,
		`rundll32.exe shell32.dll,Foo`:         `rundll32.exe`,
		`notepad`:                              `notepad`,
	}
	for in, want := range cases {
		if got := exePath(in); got != want {
			t.Errorf("exePath(%q)=%q want %q", in, got, want)
		}
	}
}

func TestParseIFileV2(t *testing.T) {
	path := `C:\Users\me\Documents\report.docx`
	u := utf16.Encode([]rune(path + "\x00"))
	b := make([]byte, 28+len(u)*2)
	binary.LittleEndian.PutUint64(b[0:], 2)
	binary.LittleEndian.PutUint64(b[8:], 12345)
	when := time.Date(2025, 5, 1, 12, 0, 0, 0, time.UTC)
	binary.LittleEndian.PutUint64(b[16:], uint64(when.UnixNano()/100+116444736000000000))
	binary.LittleEndian.PutUint32(b[24:], uint32(len(u)))
	for i, c := range u {
		binary.LittleEndian.PutUint16(b[28+2*i:], c)
	}
	orig, size, del, ok := parseIFile(b)
	if !ok || orig != path || size != 12345 || !del.Equal(when) {
		t.Fatalf("got %q %d %v %v", orig, size, del, ok)
	}
	if _, _, _, ok := parseIFile(b[:10]); ok {
		t.Fatal("short file accepted")
	}
}

func TestPathAudit(t *testing.T) {
	dir := t.TempDir()
	clean, dead, dups := pathAudit(dir + ";" + dir + `\;/definitely/not/here;;` + dir)
	if len(clean) != 1 || len(dead) != 1 || len(dups) != 2 {
		t.Fatalf("clean=%v dead=%v dups=%v", clean, dead, dups)
	}
}
