package graphics

import (
	"bytes"
	"encoding/base64"
	"strings"
	"testing"
)

func TestKittyChunks(t *testing.T) {
	payload := bytes.Repeat([]byte{0x89, 'P', 'N', 'G', 0, 1, 2, 3, 4, 5}, 500) // 5000 bytes
	out := string(EncodeKitty(payload, 1, 10, 5))

	pieces := strings.SplitAfter(out, kittyEnd)
	if pieces[len(pieces)-1] == "" {
		pieces = pieces[:len(pieces)-1]
	}
	if len(pieces) != 2 {
		t.Fatalf("got %d chunks, want 2", len(pieces))
	}
	const head = "\x1b_Ga=T,f=100,i=1,c=10,r=5,q=2,m=1;"
	if !strings.HasPrefix(pieces[0], head) {
		t.Errorf("first chunk starts %q, want %q", pieces[0][:len(head)], head)
	}
	if !strings.HasPrefix(pieces[1], "\x1b_Gm=0;") {
		t.Errorf("last chunk starts %q, want m=0", pieces[1][:8])
	}

	var b64 strings.Builder
	for _, p := range pieces {
		p = strings.TrimSuffix(p, kittyEnd)
		b64.WriteString(p[strings.IndexByte(p, ';')+1:])
	}
	decoded, err := base64.StdEncoding.DecodeString(b64.String())
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(decoded, payload) {
		t.Error("concatenated chunks do not decode to the input")
	}
}

func TestKittySingleChunk(t *testing.T) {
	out := string(EncodeKitty([]byte("tiny"), 7, 3, 2))
	want := "\x1b_Ga=T,f=100,i=7,c=3,r=2,q=2,m=0;dGlueQ==\x1b\\"
	if out != want {
		t.Errorf("EncodeKitty = %q, want %q", out, want)
	}
}

func TestKittyDelete(t *testing.T) {
	if got, want := string(KittyDelete(1)), "\x1b_Ga=d,d=I,i=1,q=2\x1b\\"; got != want {
		t.Errorf("KittyDelete(1) = %q, want %q", got, want)
	}
}
