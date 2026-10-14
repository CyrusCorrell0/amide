package graphics

import (
	"bytes"
	"encoding/base64"
	"image"
	"image/color"
	"image/png"
	"strings"
	"testing"
)

// testImage is a 20 x 10 gradient, small enough to encode quickly and varied
// enough that the encoders have more than one colour to deal with.
func testImage() *image.RGBA {
	img := image.NewRGBA(image.Rect(0, 0, 20, 10))
	for y := 0; y < 10; y++ {
		for x := 0; x < 20; x++ {
			img.Set(x, y, color.RGBA{uint8(x * 12), uint8(y * 25), 128, 255})
		}
	}
	return img
}

func TestITerm2RoundTrip(t *testing.T) {
	out := string(EncodeITerm2(EncodePNG(testImage()), 4, 2))

	const head = "\x1b]1337;File=inline=1;width=4;height=2;preserveAspectRatio=0:"
	if !strings.HasPrefix(out, head) || !strings.HasSuffix(out, "\a") {
		t.Fatalf("framing wrong: %q...", out[:min(len(out), 80)])
	}
	b64 := out[strings.IndexByte(out, ':')+1 : len(out)-1]
	raw, err := base64.StdEncoding.DecodeString(b64)
	if err != nil {
		t.Fatal(err)
	}
	img, err := png.Decode(bytes.NewReader(raw))
	if err != nil {
		t.Fatal(err)
	}
	if b := img.Bounds(); b.Dx() != 20 || b.Dy() != 10 {
		t.Errorf("decoded %dx%d, want 20x10", b.Dx(), b.Dy())
	}
}
