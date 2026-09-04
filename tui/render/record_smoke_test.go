package render

import (
	"image/color"
	"image/gif"
	"os"
	"path/filepath"
	"testing"

	"github.com/CyrusCorrell0/amide/tui/pdb"
)

func TestRecordTrajectoryGIFSmoke(t *testing.T) {
	mol, err := pdb.ParseFile(filepath.Join("..", "..", "tests", "data", "sample.pdb"))
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(mol.Frames) == 0 {
		t.Fatal("no frames")
	}

	out := filepath.Join(t.TempDir(), "smoke.gif")
	params := RecordParams{
		Mol:         mol,
		Mode:        ModeBallStick,
		ShowCartoon: false,
		Cfg: SceneConfig{
			CenterX: mol.CenterX, CenterY: mol.CenterY, CenterZ: mol.CenterZ,
			RotX: 0.3, RotY: 0.6, Zoom: 8,
		},
		TermW:      80,
		TermH:      40,
		FPS:        10,
		Background: color.RGBA{R: 0, G: 0, B: 0, A: 0xFF},
	}
	if err := RecordTrajectoryGIF(out, params); err != nil {
		t.Fatalf("record: %v", err)
	}

	f, err := os.Open(out)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer f.Close()
	g, err := gif.DecodeAll(f)
	if err != nil {
		t.Fatalf("decode gif: %v", err)
	}
	if len(g.Image) == 0 {
		t.Fatal("gif has no frames")
	}
	t.Logf("gif frames=%d size=%dx%d delay0=%d", len(g.Image), g.Image[0].Bounds().Dx(), g.Image[0].Bounds().Dy(), g.Delay[0])
}
