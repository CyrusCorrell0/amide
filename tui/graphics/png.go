package graphics

import (
	"bytes"
	"image"
	"image/png"
)

// pngEncoder trades size for speed: a frame is encoded on every rotation step
// and the terminal decodes it locally, so the milliseconds spent compressing
// matter more than the bytes on the wire.
var pngEncoder = png.Encoder{CompressionLevel: png.BestSpeed}

// EncodePNG encodes img for the kitty and iTerm2 protocols. Encoding into a
// bytes.Buffer only fails on a zero-sized image, which is a caller bug, so it
// panics rather than making every frame handle an error.
func EncodePNG(img image.Image) []byte {
	var buf bytes.Buffer
	if err := pngEncoder.Encode(&buf, img); err != nil {
		panic("graphics: png encode: " + err.Error())
	}
	return buf.Bytes()
}
