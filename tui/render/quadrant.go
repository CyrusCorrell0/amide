package render

// quadrantGlyphs is indexed by a mask with TL=8, TR=4, BL=2, BR=1.
var quadrantGlyphs = [16]string{
	" ", "▗", "▖", "▄", "▝", "▐", "▞", "▟",
	"▘", "▚", "▌", "▙", "▀", "▜", "▛", "█",
}

// RenderQuadrant draws a 2x2 pixel grid per cell, doubling the horizontal
// resolution of half blocks at the cost of two colours per cell.
func (pb *PixelBuffer) RenderQuadrant() string {
	return encodeCells(pb, 2, 2, func(px []PixelCell) (string, rgb, rgb) {
		fg, bg, m := pickTwo(px)
		// pickTwo numbers pixels in reading order; the table wants TL high.
		q := (m&1)<<3 | (m&2)<<1 | (m&4)>>1 | (m&8)>>3
		return solid(quadrantGlyphs[q], fg, bg, q, 15)
	})
}

// pickTwo reduces a cell to the two colours a glyph can show. It takes the
// most distant pair (L1 in RGB) as foreground and background, preferring a
// lit pixel for the foreground, and assigns every pixel to the nearer one.
// Bit i of mask is set when pixel i went to the foreground. A cell whose
// colours are all within 24 of each other is one colour: its first lit
// pixel, or nothing at all.
func pickTwo(px []PixelCell) (fg, bg rgb, mask uint8) {
	bi, bj, best := 0, 0, -1
	for i := range px {
		for j := i + 1; j < len(px); j++ {
			if d := dist(colour(px[i]), colour(px[j])); d > best {
				bi, bj, best = i, j, d
			}
		}
	}
	if best < 24 {
		fg, bg = background, background
		for _, p := range px {
			if p.HasColor {
				fg = colour(p)
				break
			}
		}
	} else {
		if !px[bi].HasColor && px[bj].HasColor {
			bi, bj = bj, bi
		}
		fg, bg = colour(px[bi]), colour(px[bj])
	}
	for i, p := range px {
		c := colour(p)
		if dist(c, fg) < dist(c, bg) || (fg == bg && p.HasColor) {
			mask |= 1 << i
		}
	}
	return fg, bg, mask
}

// solid normalises the two uniform masks so every one-colour cell is a full
// block on the canvas background and an empty cell is a plain space.
func solid(ch string, fg, bg rgb, mask, full uint8) (string, rgb, rgb) {
	switch mask {
	case full:
		return "█", fg, background
	case 0:
		if bg == background {
			return " ", background, background
		}
		return "█", bg, background
	}
	return ch, fg, bg
}

func dist(a, b rgb) int {
	return absDiff(a.r, b.r) + absDiff(a.g, b.g) + absDiff(a.b, b.b)
}

func absDiff(a, b uint8) int {
	if a > b {
		return int(a - b)
	}
	return int(b - a)
}
