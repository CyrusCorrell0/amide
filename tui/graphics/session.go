package graphics

// The cell size to assume when the terminal will not say: the common 8 x 16 font.
const (
	defaultCellW = 8
	defaultCellH = 16
)

// Session is everything the viewer needs to draw the pixel tier: the
// protocol, the cell size in pixels, and the overlay its frames go through.
// main builds one before Bubble Tea starts and every screen shares it.
type Session struct {
	Protocol     Protocol
	CellW, CellH int
	Overlay      *Overlay
}

// NewSession returns a session with a fresh overlay, assuming 8 x 16 cells
// when either dimension is unknown (zero).
func NewSession(proto Protocol, cellW, cellH int) *Session {
	if cellW <= 0 || cellH <= 0 {
		cellW, cellH = defaultCellW, defaultCellH
	}
	return &Session{Protocol: proto, CellW: cellW, CellH: cellH, Overlay: &Overlay{}}
}
