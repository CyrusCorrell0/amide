// Package graphics is the terminal plumbing for the pixel canvas tier: which
// image protocol the terminal speaks, how to encode a frame for it, how to slip
// the escape sequence into Bubble Tea's output without the renderer painting
// over it, and, for sixel, how big a cell is in pixels. It knows nothing about
// molecules or models, so it can be tested without a terminal.
package graphics

import "strings"

// Protocol is the inline image protocol the viewer draws the pixel tier with.
// None means the terminal gets text tiers only.
type Protocol int

const (
	None Protocol = iota
	Kitty
	Sixel
	ITerm2
)

func (p Protocol) String() string {
	switch p {
	case Kitty:
		return "kitty"
	case Sixel:
		return "sixel"
	case ITerm2:
		return "iterm2"
	default:
		return "none"
	}
}

// Detect picks a protocol from the environment alone. Querying the terminal
// (DA1, XTGETTCAP) would be more accurate but means another read on stdin
// before Bubble Tea starts; the environment is right for every terminal we
// target and AMIDE_GRAPHICS covers the rest. tmux is refused because image
// passthrough there depends on its config and usually leaves garbage behind.
func Detect(getenv func(string) string) Protocol {
	switch strings.ToLower(getenv("AMIDE_GRAPHICS")) {
	case "none":
		return None
	case "kitty":
		return Kitty
	case "sixel":
		return Sixel
	case "iterm2":
		return ITerm2
	}

	term := getenv("TERM")
	program := getenv("TERM_PROGRAM")
	switch {
	case getenv("TMUX") != "":
		return None
	case term == "xterm-kitty", getenv("KITTY_WINDOW_ID") != "":
		return Kitty
	case strings.EqualFold(program, "WezTerm"), strings.EqualFold(program, "ghostty"):
		return Kitty
	case getenv("KONSOLE_VERSION") != "":
		return Kitty
	case getenv("LC_TERMINAL") == "iTerm2", program == "iTerm.app":
		return ITerm2
	case getenv("WT_SESSION") != "", program == "vscode",
		strings.HasPrefix(term, "foot"), strings.HasPrefix(term, "mlterm"):
		return Sixel
	}
	return None
}
