package render

import "testing"

func TestRenderQuadrant(t *testing.T) {
	cases := []struct {
		name string
		px   [4]*rgb // TL, TR, BL, BR; nil is unlit
		want string
	}{
		{"diagonal", [4]*rgb{&red, nil, nil, &red}, cell("▚", red, background)},
		{"full", [4]*rgb{&red, &red, &red, &red}, cell("█", red, background)},
		{"two colours", [4]*rgb{&red, &blue, &red, &blue}, cell("▌", red, blue)},
		{"empty", [4]*rgb{}, cell(" ", background, background)},
		{"lit pixel on the right", [4]*rgb{nil, &red, nil, nil}, cell("▝", red, background)},
	}
	for _, tc := range cases {
		pb := testBuffer(1, 1, 2, 2)
		for i, c := range tc.px {
			if c != nil {
				set(pb, i%2, i/2, 0, *c)
			}
		}
		if got := pb.RenderQuadrant(); got != tc.want {
			t.Errorf("%s: got %q, want %q", tc.name, got, tc.want)
		}
	}
}
