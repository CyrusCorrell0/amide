package graphics

import (
	"bytes"
	"io"
	"os"
	"strconv"
	"time"

	"github.com/charmbracelet/x/term"
	"github.com/muesli/cancelreader"
)

// cellSizeQuery is XTWINOPS 16: report the cell size in pixels. The terminal
// answers CSI 6 ; height ; width t.
const (
	cellSizeQuery = "\x1b[16t"
	cellSizeReply = "\x1b[6;"
)

// cancelGrace is how long to wait for the reader goroutine after Cancel
// before closing the reader under it. Cancel itself gives up after 100ms on
// Windows when the read is already past its wait.
const cancelGrace = 200 * time.Millisecond

// ProbeCellSize asks the terminal for its cell size in pixels, which sixel
// needs to size an image to the canvas. It runs once, before Bubble Tea
// starts, and its one job beyond the answer is to leave stdin exactly as it
// found it: a reader still blocked on stdin after this returns would swallow
// the first key press meant for the viewer. So every exit path cancels the
// read, closes the reader and waits for the goroutine before restoring the
// terminal mode.
//
// in must be os.Stdin: cancelreader can only interrupt a blocking read on the
// stdin handle (on Windows it reopens CONIN$), so any other file returns
// ok=false rather than risk a read that cannot be stopped.
func ProbeCellSize(in *os.File, out io.Writer, timeout time.Duration) (w, h int, ok bool) {
	if in == nil || in.Fd() != os.Stdin.Fd() || !term.IsTerminal(in.Fd()) {
		return 0, 0, false
	}

	state, err := term.MakeRaw(in.Fd())
	if err != nil {
		return 0, 0, false
	}
	defer term.Restore(in.Fd(), state)

	// The reader must exist before the query goes out: on Windows creating it
	// flushes the console input buffer, which would discard an early reply.
	r, err := cancelreader.NewReader(in)
	if err != nil {
		return 0, 0, false
	}

	// The goroutine reads until the reply's final byte, an error (including
	// cancellation) or a size no reply reaches, then hands over what it read.
	done := make(chan []byte, 1)
	go func() {
		var buf []byte
		chunk := make([]byte, 64)
		for {
			n, err := r.Read(chunk)
			buf = append(buf, chunk[:n]...)
			if err != nil || bytes.IndexByte(buf, 't') >= 0 || len(buf) > 256 {
				done <- buf
				return
			}
		}
	}()

	if _, err := io.WriteString(out, cellSizeQuery); err != nil {
		stop(r, done)
		return 0, 0, false
	}
	timer := time.NewTimer(timeout)
	defer timer.Stop()
	select {
	case data := <-done:
		r.Close()
		return parseCellSize(data)
	case <-timer.C:
		stop(r, done)
		return 0, 0, false
	}
}

// stop cancels r and waits for the reader goroutine to finish, then closes r.
// If Cancel cannot interrupt the read (on Windows it gives up when the read is
// already past its wait), closing the reader closes its CONIN$ handle, which
// aborts the pending read. The last wait is bounded so that a console that
// ignores even that cannot hang startup.
func stop(r cancelreader.CancelReader, done <-chan []byte) {
	r.Cancel()
	select {
	case <-done:
		r.Close()
		return
	case <-time.After(cancelGrace):
	}
	r.Close()
	select {
	case <-done:
	case <-time.After(cancelGrace):
	}
}

// parseCellSize finds CSI 6 ; h ; w t in data and returns w, h.
func parseCellSize(data []byte) (w, h int, ok bool) {
	i := bytes.LastIndex(data, []byte(cellSizeReply))
	if i < 0 {
		return 0, 0, false
	}
	rest := data[i+len(cellSizeReply):]
	end := bytes.IndexByte(rest, 't')
	if end < 0 {
		return 0, 0, false
	}
	hs, ws, found := bytes.Cut(rest[:end], []byte{';'})
	if !found {
		return 0, 0, false
	}
	h, err1 := strconv.Atoi(string(hs))
	w, err2 := strconv.Atoi(string(ws))
	if err1 != nil || err2 != nil || w <= 0 || h <= 0 {
		return 0, 0, false
	}
	return w, h, true
}
