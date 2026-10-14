package graphics

import (
	"encoding/base64"
	"strconv"
)

// iTerm2 inline images: OSC 1337 ; File=<args> : <base64> BEL.
const (
	iterm2Start = "\x1b]1337;File="
	iterm2End   = "\a"
)

// EncodeITerm2 draws a PNG at the cursor stretched to exactly cols x rows cells.
// preserveAspectRatio=0 because the buffer was already rendered at the canvas
// aspect; letting the terminal letterbox it would shift the image off the grid.
func EncodeITerm2(png []byte, cols, rows int) []byte {
	head := iterm2Start + "inline=1;width=" + strconv.Itoa(cols) +
		";height=" + strconv.Itoa(rows) + ";preserveAspectRatio=0:"
	out := make([]byte, 0, len(head)+base64.StdEncoding.EncodedLen(len(png))+len(iterm2End))
	out = append(out, head...)
	out = base64.StdEncoding.AppendEncode(out, png)
	return append(out, iterm2End...)
}
