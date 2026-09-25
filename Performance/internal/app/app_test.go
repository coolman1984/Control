package app

import "testing"

func TestMatch(t *testing.T) {
	cases := []struct {
		p, id string
		ok    bool
	}{
		{"junk.*", "junk.user-temp.clean", true},
		{"junk.user-temp.clean", "JUNK.user-temp.clean", true},
		{"tweaks.*.apply", "tweaks.menu-delay.apply", true},
		{"tweaks.*.apply", "tweaks.menu-delay.undo", false},
		{"junk.*", "devcache.npm.clean", false},
		{"*.clean", "junk.x", false},
	}
	for _, c := range cases {
		if got := Match(c.p, c.id); got != c.ok {
			t.Errorf("Match(%q,%q)=%v want %v", c.p, c.id, got, c.ok)
		}
	}
}
