package graphics

import (
	"bytes"
	"testing"
)

func TestSixelFraming(t *testing.T) {
	out, err := EncodeSixel(testImage())
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.HasPrefix(out, []byte("\x1bP")) {
		t.Errorf("does not start with DCS: %q", out[:min(len(out), 16)])
	}
	if !bytes.HasSuffix(out, []byte("\x1b\\")) {
		t.Errorf("does not end with ST: %q", out[max(0, len(out)-16):])
	}
	if len(out) <= 4 {
		t.Errorf("empty sixel body: %q", out)
	}
}
