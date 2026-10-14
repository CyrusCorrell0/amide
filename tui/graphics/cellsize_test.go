package graphics

import (
	"bytes"
	"os"
	"testing"
	"time"
)

func TestProbeCellSizeNotATerminal(t *testing.T) {
	f, err := os.CreateTemp(t.TempDir(), "notatty")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()

	var out bytes.Buffer
	start := time.Now()
	_, _, ok := ProbeCellSize(f, &out, 300*time.Millisecond)
	if elapsed := time.Since(start); elapsed > time.Second {
		t.Errorf("took %v, want under 1s", elapsed)
	}
	if ok {
		t.Error("ok = true for a plain file")
	}
}

func TestProbeCellSizeNil(t *testing.T) {
	if _, _, ok := ProbeCellSize(nil, &bytes.Buffer{}, time.Millisecond); ok {
		t.Error("ok = true for nil input")
	}
}

func TestParseCellSize(t *testing.T) {
	cases := []struct {
		in     string
		w, h   int
		wantOK bool
	}{
		{"\x1b[6;16;8t", 8, 16, true},
		{"junk\x1b[6;20;10t", 10, 20, true},
		{"\x1b[6;16t", 0, 0, false},
		{"\x1b[6;a;8t", 0, 0, false},
		{"\x1b[6;0;8t", 0, 0, false},
		{"\x1b[6;16;8", 0, 0, false},
		{"", 0, 0, false},
	}
	for _, c := range cases {
		w, h, ok := parseCellSize([]byte(c.in))
		if w != c.w || h != c.h || ok != c.wantOK {
			t.Errorf("parseCellSize(%q) = %d, %d, %v; want %d, %d, %v", c.in, w, h, ok, c.w, c.h, c.wantOK)
		}
	}
}
