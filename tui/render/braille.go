package render

import "math"

// brailleBits maps a pixel in reading order (2 wide, 4 tall) to its dot.
var brailleBits = [8]uint8{0x01, 0x08, 0x02, 0x10, 0x04, 0x20, 0x40, 0x80}

// RenderBraille draws a 2x4 dot grid per cell, the finest text tier. Dots
// carry one colour per cell, taken from the frontmost lit pixel, so it
// reads best for wireframes and sparse scenes.
func (pb *PixelBuffer) RenderBraille() string {
	return encodeCells(pb, 2, 4, func(px []PixelCell) (string, rgb, rgb) {
		var mask uint8
		fg, front := background, math.Inf(-1)
		for i, p := range px {
			if !p.HasColor {
				continue
			}
			mask |= brailleBits[i]
			if p.Depth >= front {
				fg, front = colour(p), p.Depth
			}
		}
		if mask == 0 {
			return " ", background, background
		}
		return string(rune(0x2800 + int(mask))), fg, background
	})
}
