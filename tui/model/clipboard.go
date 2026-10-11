package model

import (
	"errors"
	"os/exec"
	"runtime"
	"strings"
)

// copyText puts text on the system clipboard; a variable so tests can swap it.
var copyText = copyToClipboard

// copyToClipboard pipes text into the platform's clipboard tool.
func copyToClipboard(text string) error {
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "windows":
		cmd = exec.Command("clip")
	case "darwin":
		cmd = exec.Command("pbcopy")
	default:
		for _, tool := range [][]string{
			{"wl-copy"},
			{"xclip", "-selection", "clipboard"},
			{"xsel", "--clipboard", "--input"},
		} {
			if _, err := exec.LookPath(tool[0]); err == nil {
				cmd = exec.Command(tool[0], tool[1:]...)
				break
			}
		}
		if cmd == nil {
			return errors.New("no clipboard tool found; install wl-clipboard or xclip")
		}
	}
	cmd.Stdin = strings.NewReader(text)
	return cmd.Run()
}
