package main

import (
	"errors"
	"fmt"
	"io/fs"
	"log"
	"os"
	"path/filepath"

	"github.com/CyrusCorrell0/amide/tui/model"
	tea "github.com/charmbracelet/bubbletea"
)

// syncWriter wraps stdout so that every Bubble Tea renderer flush (one Write per
// frame) is bracketed in DEC private mode 2026 "synchronized output" markers. The
// terminal buffers the whole frame and presents it atomically, eliminating the
// erase-line/repaint flicker the standard renderer would otherwise show during
// full-canvas repaints (playback, rotation). Terminals without 2026 support simply
// ignore the unknown private-mode sequences.
//
// It embeds *os.File so it still satisfies bubbletea's term.File check (Fd()). That
// is required on Windows to enable virtual-terminal processing and to read the
// terminal size; a plain io.Writer would break both.
type syncWriter struct {
	*os.File
}

const (
	beginSync = "\x1b[?2026h"
	endSync   = "\x1b[?2026l"
)

func (w syncWriter) Write(p []byte) (int, error) {
	buf := make([]byte, 0, len(p)+len(beginSync)+len(endSync))
	buf = append(buf, beginSync...)
	buf = append(buf, p...)
	buf = append(buf, endSync...)
	if _, err := w.File.Write(buf); err != nil {
		return 0, err
	}
	return len(p), nil
}

// resolveTarget turns the command line into the directory the file picker is
// rooted at and, when a file was named, the file to open straight away. A
// directory roots the picker and opens nothing; a file opens the viewer with the
// picker rooted at its parent, so quitting the viewer lands somewhere useful.
func resolveTarget(args []string) (dir, file string, err error) {
	switch len(args) {
	case 0:
		dir, err = os.Getwd()
		return dir, "", err
	case 1:
		info, err := os.Stat(args[0])
		if err != nil {
			var pathErr *fs.PathError
			if errors.As(err, &pathErr) {
				return "", "", fmt.Errorf("%s: %w", args[0], pathErr.Err)
			}
			return "", "", err
		}
		switch {
		case info.IsDir():
			return args[0], "", nil
		case info.Mode().IsRegular():
			return filepath.Dir(args[0]), args[0], nil
		default:
			return "", "", fmt.Errorf("%s: not a file or directory", args[0])
		}
	default:
		return "", "", errors.New("usage: amide-tui [PATH]")
	}
}

func main() {
	dir, file, err := resolveTarget(os.Args[1:])
	if err != nil {
		fmt.Fprintf(os.Stderr, "amide-tui: %v\n", err)
		os.Exit(2)
	}

	p := tea.NewProgram(
		model.NewAppModel(dir, file),
		tea.WithAltScreen(),
		tea.WithOutput(syncWriter{os.Stdout}),
	)
	if _, err := p.Run(); err != nil {
		log.Fatalf("failed to run app: %v", err)
	}
}
