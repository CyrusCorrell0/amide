package graphics

import (
	"strconv"
	"sync"
)

// Cursor framing around every overlay sequence. DECSC and DECRC (ESC 7, ESC 8)
// save and restore the cursor, so Bubble Tea's renderer, which tracks where it
// left the cursor, never notices the image was drawn somewhere else.
const (
	saveCursor    = "\x1b7"
	restoreCursor = "\x1b8"
)

// Overlay holds image escape sequences to append to Bubble Tea's output after
// each frame. The renderer only knows about text, so an image written outside
// it would be painted over on the next repaint; appending to the same flush
// keeps the image on top. The model writes it and the output writer reads it
// on another goroutine, hence the mutex.
type Overlay struct {
	mu     sync.Mutex
	sticky []byte
	once   []byte
}

// Set keeps seq at row, col (1-based) and sends it after every flush until Set
// or Clear replaces it. Sixel and iTerm2 images live in cells, so any repaint
// erases part of them and they have to be redrawn each time.
func (o *Overlay) Set(row, col int, seq []byte) {
	framed := frame(row, col, seq)
	o.mu.Lock()
	o.sticky = framed
	o.mu.Unlock()
}

// Once sends seq at row, col (1-based) on the next flush only. Kitty
// placements persist on their own, so re-sending one per flush would
// retransmit the whole image; a kitty delete goes this way too. Two calls
// before a flush are sent in order.
func (o *Overlay) Once(row, col int, seq []byte) {
	framed := frame(row, col, seq)
	o.mu.Lock()
	o.once = append(o.once, framed...)
	o.mu.Unlock()
}

// Clear drops both the pending and the kept sequences.
func (o *Overlay) Clear() {
	o.mu.Lock()
	o.sticky, o.once = nil, nil
	o.mu.Unlock()
}

// Bytes returns what to append to the current flush: the one-shot sequences,
// then the sticky one. It returns nil without allocating when there is
// nothing to send. The caller must not modify the result.
func (o *Overlay) Bytes() []byte {
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.once == nil {
		return o.sticky
	}
	out := append(o.once, o.sticky...)
	o.once = nil
	return out
}

// frame wraps seq as save cursor, CSI row;col H, seq, restore cursor.
func frame(row, col int, seq []byte) []byte {
	out := make([]byte, 0, len(seq)+len(saveCursor)+len(restoreCursor)+16)
	out = append(out, saveCursor+"\x1b["...)
	out = strconv.AppendInt(out, int64(row), 10)
	out = append(out, ';')
	out = strconv.AppendInt(out, int64(col), 10)
	out = append(out, 'H')
	out = append(out, seq...)
	return append(out, restoreCursor...)
}
