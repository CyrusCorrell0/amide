package render

import "testing"

func TestRenderBraille(t *testing.T) {
	pb := testBuffer(1, 1, 2, 4)
	set(pb, 0, 0, 0, red)
	set(pb, 1, 3, 0, red)
	if got, want := pb.RenderBraille(), cell(string(rune(0x2800|0x01|0x80)), red, background); got != want {
		t.Errorf("corner dots: got %q, want %q", got, want)
	}

	// The frontmost lit pixel sets the cell colour.
	pb = testBuffer(1, 1, 2, 4)
	set(pb, 0, 0, -1, red)
	set(pb, 1, 1, 2, blue)
	if got, want := pb.RenderBraille(), cell(string(rune(0x2800|0x01|0x10)), blue, background); got != want {
		t.Errorf("depth: got %q, want %q", got, want)
	}

	if got, want := testBuffer(1, 1, 2, 4).RenderBraille(), cell(" ", background, background); got != want {
		t.Errorf("empty: got %q, want %q", got, want)
	}
}
