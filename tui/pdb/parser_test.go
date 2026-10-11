package pdb

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const atomLines = `ATOM      1  N   GLY A   1      -6.540  -3.146   4.486  1.00  0.00           N
ATOM      2  CA  GLY A   1      -6.623  -2.213   3.659  1.00  0.00           C
ATOM      3  C   GLY A   1      -5.437  -2.241   2.606  1.00  0.00           C
`

func writePDB(t *testing.T, body string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "test.pdb")
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func frames(n int, endmdl bool) string {
	var b strings.Builder
	for i := 1; i <= n; i++ {
		b.WriteString("MODEL        1\n")
		b.WriteString(atomLines)
		if endmdl {
			b.WriteString("ENDMDL\n")
		}
	}
	return b.String()
}

func mustParse(t *testing.T, parse func(string) (*Molecule, error), path string) *Molecule {
	t.Helper()
	mol, err := parse(path)
	if err != nil {
		t.Fatal(err)
	}
	return mol
}

func TestParseFirstFrameStopsAtENDMDL(t *testing.T) {
	path := writePDB(t, frames(3, true))
	all := mustParse(t, ParseFile, path)
	first := mustParse(t, ParseFirstFrame, path)
	if len(all.Frames) != 3 {
		t.Fatalf("ParseFile frames = %d, want 3", len(all.Frames))
	}
	if len(first.Frames) != 1 {
		t.Fatalf("ParseFirstFrame frames = %d, want 1", len(first.Frames))
	}
	a, b := all.Frames[0].Atoms, first.Frames[0].Atoms
	if len(a) != len(b) {
		t.Fatalf("atoms = %d, want %d", len(b), len(a))
	}
	for i := range a {
		if a[i] != b[i] {
			t.Fatalf("atom %d = %+v, want %+v", i, b[i], a[i])
		}
	}
}

func TestParseWithoutModelRecords(t *testing.T) {
	path := writePDB(t, atomLines)
	for name, parse := range map[string]func(string) (*Molecule, error){
		"ParseFile": ParseFile, "ParseFirstFrame": ParseFirstFrame,
	} {
		if got := len(mustParse(t, parse, path).Frames); got != 1 {
			t.Errorf("%s frames = %d, want 1", name, got)
		}
	}
}

func TestParseFirstFrameStopsAtSecondMODEL(t *testing.T) {
	path := writePDB(t, frames(3, false))
	if got := len(mustParse(t, ParseFirstFrame, path).Frames); got != 1 {
		t.Fatalf("ParseFirstFrame frames = %d, want 1", got)
	}
	if got := len(mustParse(t, ParseFile, path).Frames); got != 3 {
		t.Fatalf("ParseFile frames = %d, want 3", got)
	}
}
