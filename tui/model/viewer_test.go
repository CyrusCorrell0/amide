package model

import (
	"bytes"
	"path/filepath"
	"strings"
	"testing"

	"github.com/CyrusCorrell0/amide/tui/graphics"
	"github.com/CyrusCorrell0/amide/tui/pdb"
	"github.com/CyrusCorrell0/amide/tui/render"
	tea "github.com/charmbracelet/bubbletea"
)

func newTestViewer(t *testing.T, proto graphics.Protocol) (*ViewerModel, *graphics.Session) {
	t.Helper()
	mol, err := pdb.ParseFile(filepath.Join("..", "..", "tests", "data", "sample.pdb"))
	if err != nil {
		t.Fatalf("parse sample: %v", err)
	}
	gfx := graphics.NewSession(proto, 0, 0)
	return NewViewerModel(100, 30, mol, "sample.pdb", gfx), gfx
}

func press(m *ViewerModel, k string) tea.Cmd {
	switch k {
	case "left":
		return m.Update(tea.KeyMsg{Type: tea.KeyLeft})
	case "esc":
		return m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	}
	return m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(k)})
}

func TestCanvasCyclesTextTiers(t *testing.T) {
	m, _ := newTestViewer(t, graphics.None)
	want := []render.Canvas{render.CanvasBraille, render.CanvasSextant, render.CanvasHalfBlock, render.CanvasQuadrant}
	if m.canvas != render.CanvasQuadrant {
		t.Fatalf("start canvas = %s, want quadrant", m.canvas.Name())
	}
	for _, w := range want {
		press(m, "p")
		if m.canvas != w {
			t.Fatalf("canvas = %s, want %s", m.canvas.Name(), w.Name())
		}
		x, y := w.PerCell(0, 0)
		if m.pbuf.PerCellX != x || m.pbuf.PerCellY != y {
			t.Fatalf("%s buffer grid = %dx%d, want %dx%d", w.Name(), m.pbuf.PerCellX, m.pbuf.PerCellY, x, y)
		}
	}
}

func TestKittyPixelTierSendsOnce(t *testing.T) {
	m, gfx := newTestViewer(t, graphics.Kitty)
	if m.canvas != render.CanvasPixels {
		t.Fatalf("start canvas = %s, want pixels when kitty is detected", m.canvas.Name())
	}
	// Cycle all the way round to reach pixels again through p.
	for i := 0; i < 5; i++ {
		press(m, "p")
	}
	if m.canvas != render.CanvasPixels {
		t.Fatalf("canvas after a full cycle = %s, want pixels", m.canvas.Name())
	}
	gfx.Overlay.Bytes() // drop the deletes queued while cycling
	m.View()
	got := gfx.Overlay.Bytes()
	if !bytes.HasPrefix(got, []byte("\x1b7\x1b[2;3H\x1b_G")) {
		t.Fatalf("overlay = %q..., want a kitty image at row 2 column 3", got[:min(len(got), 24)])
	}
	if !bytes.Contains(got, []byte("a=T")) {
		t.Fatalf("overlay holds no kitty transmit")
	}
	if again := gfx.Overlay.Bytes(); len(again) != 0 {
		t.Fatalf("kitty image re-sent on a flush with no change: %d bytes", len(again))
	}
	m.View()
	if again := gfx.Overlay.Bytes(); len(again) != 0 {
		t.Fatalf("kitty image re-sent by an unchanged View: %d bytes", len(again))
	}
}

func TestITerm2OverlayIsSticky(t *testing.T) {
	m, gfx := newTestViewer(t, graphics.ITerm2)
	press(m, "left")
	m.View()
	m.View()
	for i := 0; i < 2; i++ {
		if b := gfx.Overlay.Bytes(); !bytes.Contains(b, []byte("\x1b]1337;File=")) {
			t.Fatalf("flush %d: overlay lost the iTerm2 image", i+1)
		}
	}
}

func TestQuitOnKittyDeletesImage(t *testing.T) {
	m, gfx := newTestViewer(t, graphics.Kitty)
	m.View()
	gfx.Overlay.Bytes()
	press(m, "q")
	got := gfx.Overlay.Bytes()
	if !bytes.Contains(got, graphics.KittyDelete(kittyID)) {
		t.Fatalf("q left the kitty image up: overlay = %q", got)
	}
}

func TestEscOnITerm2ClearsOverlay(t *testing.T) {
	m, gfx := newTestViewer(t, graphics.ITerm2)
	m.View()
	press(m, "esc")
	if b := gfx.Overlay.Bytes(); len(b) != 0 {
		t.Fatalf("esc left %d bytes of image on the overlay", len(b))
	}
}

func TestStateLineChangesWithRotation(t *testing.T) {
	m, _ := newTestViewer(t, graphics.Kitty)
	before := m.View()
	press(m, "left")
	after := m.View()
	if before == after {
		t.Fatal("view string did not change after a rotation, so Bubble Tea would skip the flush")
	}
	if !strings.Contains(after, "rot 17 29") {
		t.Fatal("state line does not show the new rotation in degrees")
	}
}

func TestRocking(t *testing.T) {
	m, _ := newTestViewer(t, graphics.None)
	if cmd := press(m, "r"); cmd == nil {
		t.Fatal("r did not start the rock ticker")
	}
	rot := m.rotY
	if cmd := m.Update(RockTickMsg{}); cmd == nil {
		t.Fatal("rock tick did not schedule the next one")
	}
	if m.rotY == rot {
		t.Fatal("rock tick did not move rotY")
	}
	if !strings.Contains(m.View(), "rock on") {
		t.Fatal("state line does not show rock on")
	}
	press(m, "r")
	rot = m.rotY
	if cmd := m.Update(RockTickMsg{}); cmd != nil {
		t.Fatal("rock tick kept ticking after r stopped it")
	}
	if m.rotY != rot {
		t.Fatal("rock tick moved rotY after r stopped it")
	}
}

func TestRotateWhileRockingMovesBase(t *testing.T) {
	m, _ := newTestViewer(t, graphics.None)
	press(m, "r")
	base := m.rockBase
	press(m, "l")
	if m.rockBase <= base {
		t.Fatal("rotating while rocking did not move the swing centre")
	}
}

func TestTextTiersEmitNoImageEscapes(t *testing.T) {
	m, gfx := newTestViewer(t, graphics.None)
	for i := 0; i < 4; i++ {
		v := m.View()
		for _, esc := range []string{"\x1b_G", "\x1b]1337", "\x1bP"} {
			if strings.Contains(v, esc) {
				t.Fatalf("%s view contains image escape %q", m.canvas.Name(), esc)
			}
		}
		if b := gfx.Overlay.Bytes(); len(b) != 0 {
			t.Fatalf("%s tier wrote %d bytes to the overlay", m.canvas.Name(), len(b))
		}
		press(m, "p")
	}
}

func TestNilSessionIsTextOnly(t *testing.T) {
	mol, err := pdb.ParseFile(filepath.Join("..", "..", "tests", "data", "sample.pdb"))
	if err != nil {
		t.Fatalf("parse sample: %v", err)
	}
	m := NewViewerModel(100, 30, mol, "sample.pdb", nil)
	for i := 0; i < 5; i++ {
		if m.canvas == render.CanvasPixels {
			t.Fatal("reached the pixel tier without a session")
		}
		m.View()
		press(m, "p")
	}
}
