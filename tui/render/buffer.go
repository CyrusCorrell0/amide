package render

import (
	"fmt"
	"math"
	"strings"

	"github.com/charmbracelet/lipgloss"
)

type Cell struct {
	Ch    rune
	FG    lipgloss.Color
	Depth float64
}

type ScreenBuffer struct {
	Width  int
	Height int
	Cells  [][]Cell
}

func NewScreenBuffer(width, height int) *ScreenBuffer {
	if width < 1 {
		width = 1
	}
	if height < 1 {
		height = 1
	}
	b := &ScreenBuffer{
		Width:  width,
		Height: height,
		Cells:  make([][]Cell, height),
	}
	for y := 0; y < height; y++ {
		b.Cells[y] = make([]Cell, width)
	}
	b.Clear()
	return b
}

func (b *ScreenBuffer) Resize(width, height int) {
	if width == b.Width && height == b.Height {
		return
	}
	nb := NewScreenBuffer(width, height)
	*b = *nb
}

func (b *ScreenBuffer) Clear() {
	for y := 0; y < b.Height; y++ {
		for x := 0; x < b.Width; x++ {
			b.Cells[y][x] = Cell{
				Ch:    ' ',
				FG:    lipgloss.Color("#8B949E"),
				Depth: math.Inf(-1),
			}
		}
	}
}

func (b *ScreenBuffer) Set(x, y int, depth float64, ch rune, fg lipgloss.Color) {
	if x < 0 || x >= b.Width || y < 0 || y >= b.Height {
		return
	}
	if depth < b.Cells[y][x].Depth {
		return
	}
	b.Cells[y][x] = Cell{Ch: ch, FG: fg, Depth: depth}
}

// Render serialises the buffer into a string of ANSI true-colour escape sequences.
// Adjacent cells sharing the same foreground colour are grouped under a single escape
// code instead of wrapping every cell individually, reducing output by ~10× for
// typical molecule renders and eliminating most of the per-frame flash.
func (b *ScreenBuffer) Render() string {
	var sb strings.Builder
	// Generous estimate: most cells share colours, average ~4 bytes each + resets.
	sb.Grow((b.Width + 1) * b.Height * 5)

	var curR, curG, curBv uint8
	fresh := true

	for y := 0; y < b.Height; y++ {
		fresh = true
		for x := 0; x < b.Width; x++ {
			cell := b.Cells[y][x]
			r, g, bv := ParseHexColor(string(cell.FG))
			if fresh || r != curR || g != curG || bv != curBv {
				fmt.Fprintf(&sb, "\x1b[38;2;%d;%d;%dm", r, g, bv)
				curR, curG, curBv = r, g, bv
				fresh = false
			}
			sb.WriteRune(cell.Ch)
		}
		sb.WriteString("\x1b[0m")
		if y < b.Height-1 {
			sb.WriteByte('\n')
		}
	}
	return sb.String()
}
