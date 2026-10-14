package render

import "strings"

// Canvas is a text tier: a pixel grid per terminal cell and the encoder that
// turns that grid into glyphs. The viewer flips one value to change tier, and
// the buffer size, the encoder and the status-bar name follow from it.
type Canvas int

const (
	CanvasHalfBlock Canvas = iota
	CanvasQuadrant
	CanvasBraille
	CanvasSextant
	CanvasPixels
)

func (c Canvas) Name() string {
	switch c {
	case CanvasQuadrant:
		return "quadrant"
	case CanvasBraille:
		return "braille"
	case CanvasSextant:
		return "sextant"
	case CanvasPixels:
		return "pixels"
	default:
		return "half block"
	}
}

// PerCell is the pixel grid one terminal cell holds. Only the pixel tier
// depends on the terminal, whose cell size in pixels is cellW by cellH.
func (c Canvas) PerCell(cellW, cellH int) (x, y int) {
	switch c {
	case CanvasQuadrant:
		return 2, 2
	case CanvasBraille:
		return 2, 4
	case CanvasSextant:
		return 2, 3
	case CanvasPixels:
		return max(1, cellW), max(1, cellH)
	default:
		return 1, 2
	}
}

// Encode renders pb as text. The pixel tier is drawn by a graphics protocol
// rather than glyphs, so it has no text form and returns "".
func (c Canvas) Encode(pb *PixelBuffer) string {
	switch c {
	case CanvasQuadrant:
		return pb.RenderQuadrant()
	case CanvasBraille:
		return pb.RenderBraille()
	case CanvasSextant:
		return pb.RenderSextant()
	case CanvasPixels:
		return ""
	default:
		return pb.RenderHalfBlock()
	}
}

// Next cycles through the tiers, skipping pixels on terminals that cannot
// show them.
func (c Canvas) Next(havePixels bool) Canvas {
	n := (c + 1) % (CanvasPixels + 1)
	if n == CanvasPixels && !havePixels {
		return CanvasHalfBlock
	}
	return n
}

type rgb struct{ r, g, b uint8 }

var background = rgb{pixelBgR, pixelBgG, pixelBgB}

// colour is what a pixel shows: its own colour when lit, the canvas otherwise.
func colour(p PixelCell) rgb {
	if !p.HasColor {
		return background
	}
	return rgb{p.R, p.G, p.B}
}

// encodeCells is the loop every glyph encoder shares. It hands glyph the
// gw by gh pixels under each terminal cell in row-major order (pixels past
// the buffer edge come through unlit) and writes the returned glyph with
// run-length compressed colour escapes, one line per terminal row.
func encodeCells(pb *PixelBuffer, gw, gh int, glyph func(px []PixelCell) (string, rgb, rgb)) string {
	var sb strings.Builder
	sb.Grow((pb.TermW*6 + 5) * pb.TermH)
	px := make([]PixelCell, gw*gh)

	for row := 0; row < pb.TermH; row++ {
		var cur rgb
		var curBg rgb
		fresh := true
		for col := 0; col < pb.TermW; col++ {
			for dy := 0; dy < gh; dy++ {
				y := row*gh + dy
				for dx := 0; dx < gw; dx++ {
					x := col*gw + dx
					if x < pb.PWidth && y < pb.PHeight {
						px[dy*gw+dx] = pb.Pixels[y*pb.PWidth+x]
					} else {
						px[dy*gw+dx] = PixelCell{}
					}
				}
			}
			ch, fg, bg := glyph(px)
			if fresh || fg != cur || bg != curBg {
				writeAnsiRGBPair(&sb, fg.r, fg.g, fg.b, bg.r, bg.g, bg.b)
				cur, curBg, fresh = fg, bg, false
			}
			sb.WriteString(ch)
		}
		sb.WriteString("\x1b[0m")
		if row < pb.TermH-1 {
			sb.WriteByte('\n')
		}
	}
	return sb.String()
}
