package main

import (
	"errors"
	"fmt"
	"io/fs"
	"log"
	"os"
	"path/filepath"
	"time"

	"github.com/CyrusCorrell0/amide/tui/graphics"
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
//
// The overlay rides in the same brackets: the pixel tier's image escape goes out
// after the frame's text, so the text never paints over it and both appear at once.
type syncWriter struct {
	*os.File
	overlay *graphics.Overlay
}

const (
	beginSync = "\x1b[?2026h"
	endSync   = "\x1b[?2026l"
)

func (w syncWriter) Write(p []byte) (int, error) {
	img := w.overlay.Bytes()
	buf := make([]byte, 0, len(p)+len(img)+len(beginSync)+len(endSync))
	buf = append(buf, beginSync...)
	buf = append(buf, p...)
	buf = append(buf, img...)
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
// `--search ROWS.json` returns only rows, the file `amide search` wrote.
func resolveTarget(args []string) (dir, file, rows string, err error) {
	if len(args) > 0 && args[0] == "--search" {
		if len(args) != 2 {
			return "", "", "", errUsage
		}
		return "", "", args[1], nil
	}
	switch len(args) {
	case 0:
		dir, err = os.Getwd()
		return dir, "", "", err
	case 1:
		info, err := os.Stat(args[0])
		if err != nil {
			var pathErr *fs.PathError
			if errors.As(err, &pathErr) {
				return "", "", "", fmt.Errorf("%s: %w", args[0], pathErr.Err)
			}
			return "", "", "", err
		}
		switch {
		case info.IsDir():
			return args[0], "", "", nil
		case info.Mode().IsRegular():
			return filepath.Dir(args[0]), args[0], "", nil
		default:
			return "", "", "", fmt.Errorf("%s: not a file or directory", args[0])
		}
	default:
		return "", "", "", errUsage
	}
}

var errUsage = errors.New("usage: amide-tui [PATH | --search ROWS.json]")

func fail(err error) {
	fmt.Fprintf(os.Stderr, "amide-tui: %v\n", err)
	os.Exit(2)
}

func main() {
	dir, file, rows, err := resolveTarget(os.Args[1:])
	if err != nil {
		fail(err)
	}

	// Kitty and iTerm2 place images by cells; only sixel needs the cell size in
	// pixels, and asking costs a round trip on stdin, so only sixel asks.
	proto := graphics.Detect(os.Getenv)
	cellW, cellH := 0, 0
	if proto == graphics.Sixel {
		if w, h, ok := graphics.ProbeCellSize(os.Stdin, os.Stdout, 300*time.Millisecond); ok {
			cellW, cellH = w, h
		}
	}
	session := graphics.NewSession(proto, cellW, cellH)

	app := model.NewAppModel(dir, file, session)
	if rows != "" {
		doc, err := model.LoadRows(rows)
		if err != nil {
			fail(err)
		}
		app = model.NewSearchApp(doc, session)
	}

	p := tea.NewProgram(
		app,
		tea.WithAltScreen(),
		tea.WithOutput(syncWriter{os.Stdout, session.Overlay}),
	)
	if _, err := p.Run(); err != nil {
		log.Fatalf("failed to run app: %v", err)
	}
}
