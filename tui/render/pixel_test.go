package render

import (
	"testing"

	"github.com/CyrusCorrell0/amide/tui/pdb"
)

func TestNewPixelBufferGrid(t *testing.T) {
	pb := NewPixelBuffer(10, 5, 2, 4)
	if pb.PWidth != 20 || pb.PHeight != 20 || len(pb.Pixels) != 400 {
		t.Fatalf("got PWidth %d PHeight %d len %d, want 20 20 400", pb.PWidth, pb.PHeight, len(pb.Pixels))
	}
}

func TestProjectCentreOnEveryGrid(t *testing.T) {
	atoms := []pdb.Atom{{Serial: 1, Element: "C", X: 1, Y: 2, Z: 3}}
	cfg := SceneConfig{CenterX: 1, CenterY: 2, CenterZ: 3, RotX: 0.4, RotY: 0.7, Zoom: 8}
	for _, grid := range [][2]int{{1, 2}, {2, 2}} {
		pb := NewPixelBuffer(40, 20, grid[0], grid[1])
		pts, _ := projectAtomsPixel(atoms, pb, cfg)
		if len(pts) != 1 {
			t.Fatalf("grid %v: got %d points", grid, len(pts))
		}
		if pts[0].SX != pb.PWidth/2 || pts[0].SY != pb.PHeight/2 {
			t.Errorf("grid %v: atom at (%d,%d), want (%d,%d)", grid, pts[0].SX, pts[0].SY, pb.PWidth/2, pb.PHeight/2)
		}
	}
}

func TestDiskIsEllipseOnSquareGrid(t *testing.T) {
	// On a 2x2 grid ScaleX is 2 and ScaleY is 1, so radius 3 becomes 6 by 3 pixels.
	pb := NewPixelBuffer(20, 10, 2, 2)
	drawDiskPixel(pb, 20, 10, 0, "#FFFFFF", 3)
	minX, maxX, minY, maxY := pb.PWidth, -1, pb.PHeight, -1
	for y := 0; y < pb.PHeight; y++ {
		for x := 0; x < pb.PWidth; x++ {
			if pb.Pixels[y*pb.PWidth+x].HasColor {
				minX, maxX = min(minX, x), max(maxX, x)
				minY, maxY = min(minY, y), max(maxY, y)
			}
		}
	}
	w, h := maxX-minX+1, maxY-minY+1
	if w != 13 || h != 7 {
		t.Fatalf("lit extent %dx%d, want 13x7", w, h)
	}
}

func TestRenderHalfBlockTopPixel(t *testing.T) {
	pb := NewPixelBuffer(1, 1, 1, 2)
	pb.SetPixel(0, 0, 0, 255, 0, 0)
	want := "\x1b[38;2;255;0;0m\x1b[48;2;0;0;0m▀\x1b[0m"
	if got := pb.RenderHalfBlock(); got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}
