package model

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

const tinyPDB = `ATOM      1  N   GLY A   1      -6.540  -3.146   4.486  1.00  0.00           N
ATOM      2  CA  GLY A   1      -6.623  -2.213   3.659  1.00  0.00           C
ATOM      3  C   GLY A   1      -5.437  -2.241   2.606  1.00  0.00           C
END
`

func strp(s string) *string { return &s }

func testRows(t *testing.T) []Row {
	t.Helper()
	dir := t.TempDir()
	rows := []Row{
		{ID: strp("2LYZ"), Frames: 1, Atoms: 3, Chains: []string{"A"}},
		{ID: nil, Frames: 500, Atoms: 1, Chains: []string{"A", "B"}},
		{ID: strp("1UBQ"), Frames: 20, Atoms: 2, Chains: []string{"A"}},
	}
	for i, name := range []string{"b.pdb", "c.pdb", "a.pdb"} {
		rows[i].Path = filepath.Join(dir, name)
		if err := os.WriteFile(rows[i].Path, []byte(tinyPDB), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return rows
}

func key(s string) tea.KeyMsg { return tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(s)} }

// loadPreview drives the model through the debounce tick and the parse to a shown preview.
func loadPreview(t *testing.T, m *SearchModel) {
	t.Helper()
	tick := m.Init()()
	loaded := m.Update(tick)()
	m.Update(loaded)
	if m.preview == nil || m.preview.mol == nil {
		t.Fatalf("no preview after load: %+v", m.preview)
	}
}

func TestLoadRowsRoundTrip(t *testing.T) {
	path := filepath.Join(t.TempDir(), "rows.json")
	doc := `{"query": "frames:2..", "sort": "frames", "reverse": true, "rows": [{"path": "/x/a.pdb", "id": null, "title": null,
		"frames": 3, "atoms": 10, "hydrogens": 2, "chains": ["A", "B"], "residues": 4,
		"extent": [1.5, 2.5, 3.5], "size": 2048, "modified": 1.5, "indexed": 2.5}]}`
	if err := os.WriteFile(path, []byte(doc), 0o644); err != nil {
		t.Fatal(err)
	}
	got, err := LoadRows(path)
	if err != nil {
		t.Fatal(err)
	}
	if got.Query != "frames:2.." || got.Sort != "frames" || !got.Reverse || len(got.Rows) != 1 {
		t.Fatalf("doc did not round-trip: %+v", got)
	}
	r := got.Rows[0]
	if r.ID != nil || r.Title != nil || r.Path != "/x/a.pdb" || r.Frames != 3 || r.Atoms != 10 ||
		r.Hydrogens != 2 || strings.Join(r.Chains, "") != "AB" || r.Residues != 4 ||
		r.Extent != [3]float64{1.5, 2.5, 3.5} || r.Size != 2048 || r.Modified != 1.5 || r.Indexed != 2.5 {
		t.Fatalf("row did not round-trip: %+v", r)
	}
}

func TestLoadRowsDefaultsSortToPath(t *testing.T) {
	path := filepath.Join(t.TempDir(), "rows.json")
	if err := os.WriteFile(path, []byte(`{"query": "", "rows": []}`), 0o644); err != nil {
		t.Fatal(err)
	}
	got, err := LoadRows(path)
	if err != nil || got.Sort != "path" || got.Reverse {
		t.Fatalf("got %+v, %v", got, err)
	}
}

func TestOpensInGivenOrderAndNamesFileSort(t *testing.T) {
	rows := testRows(t)
	m := NewSearchModel(RowsFile{Rows: rows, Sort: "residues", Reverse: true}, 120, 30)
	for i, it := range m.list.Items() {
		if it.(searchItem).row.Path != rows[i].Path {
			t.Fatalf("row %d reordered on open", i)
		}
	}
	if !strings.Contains(m.View(), "s sort: residues") {
		t.Fatal("status line should name the file's sort key")
	}
	m.Update(key("s"))
	if sortKeys[m.sortKey] != "size" || !m.reverse {
		t.Fatalf("s should advance from residues to size and keep reverse; got %q %v", sortKeys[m.sortKey], m.reverse)
	}
}

func TestSortCyclesAndReverseKeepsSelection(t *testing.T) {
	rows := testRows(t)
	m := NewSearchModel(RowsFile{Rows: rows, Sort: "path"}, 120, 30)
	m.list.Select(1)
	want := m.SelectedPath()
	for i, k := range []string{"id", "frames", "atoms", "hydrogens", "residues", "size", "modified", "path"} {
		m.Update(key("s"))
		if got := sortKeys[m.sortKey]; got != k {
			t.Fatalf("press %d: sort %q, want %q", i+1, got, k)
		}
		if m.SelectedPath() != want {
			t.Fatalf("sort %q moved selection to %q", k, m.SelectedPath())
		}
	}
	first := m.list.Items()[0].(searchItem).row.Path
	m.Update(key("r"))
	if !m.reverse || m.SelectedPath() != want {
		t.Fatalf("reverse %v, selection %q", m.reverse, m.SelectedPath())
	}
	if last := m.list.Items()[len(rows)-1].(searchItem).row.Path; last != first {
		t.Fatalf("reverse did not flip order")
	}
}

func TestMissingIDSortsLastBothWays(t *testing.T) {
	m := NewSearchModel(RowsFile{Rows: testRows(t), Sort: "path"}, 120, 30)
	m.Update(key("s")) // path to id
	for _, rev := range []bool{false, true} {
		items := m.list.Items()
		if items[len(items)-1].(searchItem).row.ID != nil {
			t.Fatalf("reverse=%v: nil id not last", rev)
		}
		m.Update(key("r"))
	}
}

func TestStalePreviewIsDropped(t *testing.T) {
	m := NewSearchModel(RowsFile{Rows: testRows(t), Sort: "path"}, 120, 30)
	loadPreview(t, m)
	shown := m.preview
	stale := m.list.Items()[2].(searchItem).row.Path
	m.Update(previewLoadedMsg{path: stale})
	if m.preview != shown {
		t.Fatal("a preview for a row no longer highlighted replaced the shown one")
	}
}

func TestPreviewOnlyWhenWide(t *testing.T) {
	narrow := NewSearchModel(RowsFile{Rows: testRows(t), Sort: "path"}, 50, 30)
	loadPreview(t, narrow)
	if strings.Contains(narrow.View(), "╭") {
		t.Fatal("50 columns should have no canvas")
	}
	wide := NewSearchModel(RowsFile{Rows: testRows(t), Sort: "path"}, 120, 30)
	loadPreview(t, wide)
	if v := wide.View(); !strings.Contains(v, "╭") || !strings.Contains(v, "b.pdb") {
		t.Fatalf("120 columns should show the canvas and facts:\n%s", v)
	}
}

func TestViewFitsHeight(t *testing.T) {
	for _, size := range [][2]int{{120, 30}, {120, 8}, {80, 3}, {50, 12}, {200, 1}} {
		for _, rows := range [][]Row{testRows(t), nil} {
			m := NewSearchModel(RowsFile{Rows: rows, Query: "chains:A", Sort: "path"}, size[0], size[1])
			if rows != nil {
				loadPreview(t, m)
			}
			if n := strings.Count(m.View(), "\n") + 1; n > size[1] {
				t.Fatalf("%dx%d with %d rows: %d lines", size[0], size[1], len(rows), n)
			}
		}
	}
}

func TestEmptyRows(t *testing.T) {
	m := NewSearchModel(RowsFile{Sort: "path"}, 120, 30)
	if m.Init() != nil || m.SelectedPath() != "" {
		t.Fatal("empty screen should schedule nothing and select nothing")
	}
	if !strings.Contains(m.View(), "nothing indexed matches") {
		t.Fatal("empty screen should say nothing matches")
	}
}

func TestColumnHeaderNamesTheColumns(t *testing.T) {
	m := NewSearchModel(RowsFile{Rows: testRows(t)}, 120, 30)
	view := m.View()
	for _, col := range []string{"PDB", "FRAMES", "ATOMS", "CHAINS"} {
		if !strings.Contains(view, col) {
			t.Fatalf("header lacks %s:\n%s", col, view)
		}
	}
}

func TestCCopiesTheHighlightedPath(t *testing.T) {
	var got string
	old := copyText
	copyText = func(s string) error { got = s; return nil }
	defer func() { copyText = old }()

	m := NewSearchModel(RowsFile{Rows: testRows(t)}, 120, 30)
	m.Update(key("j"))
	cmd := m.Update(key("c"))
	if cmd == nil {
		t.Fatal("c should return a copy command")
	}
	m.Update(cmd())
	if got != m.SelectedPath() {
		t.Fatalf("copied %q, highlighted %q", got, m.SelectedPath())
	}
	if !strings.Contains(m.View(), "copied "+got) {
		t.Fatal("status line should confirm the copy")
	}
	m.Update(noticeClearMsg{})
	if !strings.Contains(m.View(), "c copy path") {
		t.Fatal("status line should return to the key help")
	}
}
