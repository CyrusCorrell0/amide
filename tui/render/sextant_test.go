package render

import "testing"

func TestSextantGlyph(t *testing.T) {
	cases := []struct {
		mask uint8
		want rune
	}{
		{0, ' '},
		{1, 0x1FB00},
		{21, 0x258C},
		{22, 0x1FB14},
		{42, 0x2590},
		{43, 0x1FB28},
		{62, 0x1FB3B},
		{63, 0x2588},
	}
	for _, tc := range cases {
		if got := sextantGlyph(tc.mask); got != string(tc.want) {
			t.Errorf("mask %d: got %U, want %U", tc.mask, []rune(got)[0], tc.want)
		}
	}
}

func TestRenderSextant(t *testing.T) {
	// Left column lit: TL, ML, BL is mask 21, the left half block.
	pb := testBuffer(1, 1, 2, 3)
	for y := 0; y < 3; y++ {
		set(pb, 0, y, 0, red)
	}
	if got, want := pb.RenderSextant(), cell("▌", red, background); got != want {
		t.Errorf("left column: got %q, want %q", got, want)
	}

	pb = testBuffer(1, 1, 2, 3)
	set(pb, 1, 0, 0, red)
	if got, want := pb.RenderSextant(), cell(string(rune(0x1FB01)), red, background); got != want {
		t.Errorf("top right: got %q, want %q", got, want)
	}
}
