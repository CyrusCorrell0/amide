package render

// RenderSextant draws a 2x3 grid per cell with the Symbols for Legacy
// Computing sextants. It needs a font that has them; the quadrant tier is
// the fallback when it does not.
func (pb *PixelBuffer) RenderSextant() string {
	return encodeCells(pb, 2, 3, func(px []PixelCell) (string, rgb, rgb) {
		fg, bg, m := pickTwo(px)
		return solid(sextantGlyph(m), fg, bg, m, 63)
	})
}

// sextantGlyph maps a mask with TL=1, TR=2, ML=4, MR=8, BL=16, BR=32 to its
// glyph. The Unicode block omits the four masks that already exist as space,
// left half, right half and full block, so later code points shift down.
func sextantGlyph(m uint8) string {
	switch m {
	case 0:
		return " "
	case 21:
		return "▌"
	case 42:
		return "▐"
	case 63:
		return "█"
	}
	r := 0x1FB00 + int(m) - 1
	if m > 21 {
		r--
	}
	if m > 42 {
		r--
	}
	return string(rune(r))
}
