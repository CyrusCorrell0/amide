package render

import (
	"math"
	"strings"
	"testing"
)

// testBuffer builds a cleared buffer as a struct literal so these tests do
// not depend on the constructor's signature.
func testBuffer(termW, termH, perX, perY int) *PixelBuffer {
	pb := &PixelBuffer{
		TermW: termW, TermH: termH,
		PerCellX: perX, PerCellY: perY,
		PWidth: termW * perX, PHeight: termH * perY,
	}
	pb.Pixels = make([]PixelCell, pb.PWidth*pb.PHeight)
	for i := range pb.Pixels {
		pb.Pixels[i] = PixelCell{Depth: math.Inf(-1)}
	}
	return pb
}

var (
	red  = rgb{255, 0, 0}
	blue = rgb{0, 0, 255}
)

func set(pb *PixelBuffer, x, y int, depth float64, c rgb) {
	pb.SetPixel(x, y, depth, c.r, c.g, c.b)
}

// cell is the exact output of a one-cell buffer.
func cell(ch string, fg, bg rgb) string {
	var sb strings.Builder
	writeAnsiRGBPair(&sb, fg.r, fg.g, fg.b, bg.r, bg.g, bg.b)
	return sb.String() + ch + "\x1b[0m"
}

func TestCanvasNext(t *testing.T) {
	if got := CanvasSextant.Next(false); got != CanvasHalfBlock {
		t.Errorf("sextant.Next(false) = %v, want half block", got.Name())
	}
	if got := CanvasSextant.Next(true); got != CanvasPixels {
		t.Errorf("sextant.Next(true) = %v, want pixels", got.Name())
	}
	if got := CanvasPixels.Next(true); got != CanvasHalfBlock {
		t.Errorf("pixels.Next(true) = %v, want half block", got.Name())
	}
	if got := CanvasHalfBlock.Next(false); got != CanvasQuadrant {
		t.Errorf("half block.Next(false) = %v, want quadrant", got.Name())
	}
}

func TestCanvasPerCell(t *testing.T) {
	cases := []struct {
		c    Canvas
		x, y int
	}{
		{CanvasHalfBlock, 1, 2},
		{CanvasQuadrant, 2, 2},
		{CanvasBraille, 2, 4},
		{CanvasSextant, 2, 3},
		{CanvasPixels, 9, 18},
	}
	for _, tc := range cases {
		if x, y := tc.c.PerCell(9, 18); x != tc.x || y != tc.y {
			t.Errorf("%s.PerCell = %d,%d, want %d,%d", tc.c.Name(), x, y, tc.x, tc.y)
		}
	}
}

func TestEncodersEmptyBuffer(t *testing.T) {
	for _, c := range []Canvas{CanvasHalfBlock, CanvasQuadrant, CanvasBraille, CanvasSextant} {
		x, y := c.PerCell(0, 0)
		out := c.Encode(testBuffer(3, 2, x, y))
		lines := strings.Split(out, "\n")
		if len(lines) != 2 {
			t.Errorf("%s: %d lines, want 2", c.Name(), len(lines))
		}
		for i, l := range lines {
			if !strings.HasSuffix(l, "\x1b[0m") {
				t.Errorf("%s: line %d does not end in a reset: %q", c.Name(), i, l)
			}
		}
	}
	if out := CanvasPixels.Encode(testBuffer(3, 2, 8, 16)); out != "" {
		t.Errorf("pixels.Encode = %q, want empty", out)
	}
}
