package graphics

import (
	"bytes"
	"testing"
)

func TestOverlaySetIsSticky(t *testing.T) {
	var o Overlay
	o.Set(2, 3, []byte("IMG"))
	want := []byte("\x1b7\x1b[2;3HIMG\x1b8")
	for i := 0; i < 2; i++ {
		if got := o.Bytes(); !bytes.Equal(got, want) {
			t.Errorf("flush %d: Bytes = %q, want %q", i, got, want)
		}
	}
}

func TestOverlayOnceThenSticky(t *testing.T) {
	var o Overlay
	o.Set(2, 3, []byte("IMG"))
	o.Once(1, 1, []byte("DEL"))
	first := o.Bytes()
	if want := []byte("\x1b7\x1b[1;1HDEL\x1b8\x1b7\x1b[2;3HIMG\x1b8"); !bytes.Equal(first, want) {
		t.Errorf("first flush = %q, want %q", first, want)
	}
	if second := o.Bytes(); bytes.Contains(second, []byte("DEL")) {
		t.Errorf("Once sent twice: %q", second)
	}
}

func TestOverlayOnceAlone(t *testing.T) {
	var o Overlay
	o.Once(2, 3, []byte("K"))
	if got := o.Bytes(); !bytes.Equal(got, []byte("\x1b7\x1b[2;3HK\x1b8")) {
		t.Errorf("Bytes = %q", got)
	}
	if got := o.Bytes(); got != nil {
		t.Errorf("after Once flushed, Bytes = %q, want nil", got)
	}
}

func TestOverlayClear(t *testing.T) {
	var o Overlay
	o.Set(2, 3, []byte("IMG"))
	o.Once(2, 3, []byte("DEL"))
	o.Clear()
	if got := o.Bytes(); got != nil {
		t.Errorf("after Clear, Bytes = %q, want nil", got)
	}
}

func TestOverlayEmptyDoesNotAllocate(t *testing.T) {
	var o Overlay
	if o.Bytes() != nil {
		t.Fatal("empty overlay returned bytes")
	}
	if n := testing.AllocsPerRun(100, func() { o.Bytes() }); n != 0 {
		t.Errorf("empty Bytes allocates %v times", n)
	}
}
