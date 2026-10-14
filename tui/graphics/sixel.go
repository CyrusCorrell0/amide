package graphics

import (
	"bytes"
	"image"

	"github.com/mattn/go-sixel"
)

// EncodeSixel encodes img as a DCS sixel string (ESC P ... ESC \). Dithering is off:
// a frame is a handful of element colours times shading, which fits the
// default adaptive palette, and dither noise would shimmer between rotation
// frames.
func EncodeSixel(img image.Image) ([]byte, error) {
	var buf bytes.Buffer
	enc := sixel.NewEncoder(&buf)
	enc.Dither = false
	if err := enc.Encode(img); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}
