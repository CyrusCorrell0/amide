package graphics

import "testing"

func TestDetect(t *testing.T) {
	cases := []struct {
		name string
		env  map[string]string
		want Protocol
	}{
		{"empty", nil, None},
		{"override none beats kitty", map[string]string{"AMIDE_GRAPHICS": "none", "TERM": "xterm-kitty"}, None},
		{"override kitty", map[string]string{"AMIDE_GRAPHICS": "kitty"}, Kitty},
		{"override sixel any case", map[string]string{"AMIDE_GRAPHICS": "SiXeL"}, Sixel},
		{"override iterm2", map[string]string{"AMIDE_GRAPHICS": "iterm2"}, ITerm2},
		{"override beats tmux", map[string]string{"AMIDE_GRAPHICS": "kitty", "TMUX": "/tmp/tmux"}, Kitty},
		{"unknown override ignored", map[string]string{"AMIDE_GRAPHICS": "bogus", "WT_SESSION": "x"}, Sixel},
		{"tmux beats kitty", map[string]string{"TMUX": "/tmp/tmux", "TERM": "xterm-kitty"}, None},
		{"TERM xterm-kitty", map[string]string{"TERM": "xterm-kitty"}, Kitty},
		{"KITTY_WINDOW_ID", map[string]string{"KITTY_WINDOW_ID": "1"}, Kitty},
		{"WezTerm", map[string]string{"TERM_PROGRAM": "WezTerm"}, Kitty},
		{"ghostty any case", map[string]string{"TERM_PROGRAM": "Ghostty"}, Kitty},
		{"Konsole", map[string]string{"KONSOLE_VERSION": "230804"}, Kitty},
		{"LC_TERMINAL iTerm2", map[string]string{"LC_TERMINAL": "iTerm2"}, ITerm2},
		{"iTerm.app", map[string]string{"TERM_PROGRAM": "iTerm.app"}, ITerm2},
		{"Windows Terminal", map[string]string{"WT_SESSION": "abc"}, Sixel},
		{"vscode", map[string]string{"TERM_PROGRAM": "vscode"}, Sixel},
		{"foot", map[string]string{"TERM": "foot-extra"}, Sixel},
		{"mlterm", map[string]string{"TERM": "mlterm"}, Sixel},
		{"plain xterm", map[string]string{"TERM": "xterm-256color"}, None},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := Detect(func(k string) string { return c.env[k] })
			if got != c.want {
				t.Errorf("Detect = %v, want %v", got, c.want)
			}
		})
	}
}

func TestProtocolString(t *testing.T) {
	for p, want := range map[Protocol]string{None: "none", Kitty: "kitty", Sixel: "sixel", ITerm2: "iterm2"} {
		if got := p.String(); got != want {
			t.Errorf("%d.String() = %q, want %q", int(p), got, want)
		}
	}
}

func TestNewSessionDefaultsCellSize(t *testing.T) {
	s := NewSession(Sixel, 0, 20)
	if s.CellW != 8 || s.CellH != 16 || s.Overlay == nil || s.Protocol != Sixel {
		t.Errorf("NewSession(Sixel, 0, 20) = %+v", s)
	}
	if s := NewSession(Kitty, 10, 20); s.CellW != 10 || s.CellH != 20 {
		t.Errorf("NewSession(Kitty, 10, 20) = %+v", s)
	}
}
