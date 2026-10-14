package render

import (
	"math"
	"testing"

	"github.com/CyrusCorrell0/amide/tui/pdb"
)

func TestDepthCueFadesFarPixel(t *testing.T) {
	pb := NewPixelBuffer(2, 1, 1, 1)
	pb.SetPixel(0, 0, 0, 200, 200, 200)  // far
	pb.SetPixel(1, 0, 10, 200, 200, 200) // near
	pb.DepthCue(0.35)
	if got := pb.Pixels[0].R; got != 70 {
		t.Errorf("far pixel R = %d, want 70", got)
	}
	if got := pb.Pixels[1].R; got != 200 {
		t.Errorf("near pixel R = %d, want 200", got)
	}
}

func TestDepthCueFlatSceneUnchanged(t *testing.T) {
	pb := NewPixelBuffer(2, 1, 1, 1)
	pb.SetPixel(0, 0, 3, 200, 200, 200)
	pb.SetPixel(1, 0, 3, 200, 200, 200)
	pb.DepthCue(0.35)
	if pb.Pixels[0].R != 200 || pb.Pixels[1].R != 200 {
		t.Fatalf("flat scene changed: %d %d", pb.Pixels[0].R, pb.Pixels[1].R)
	}
}

func TestOutlineDarkensNearSideOfStep(t *testing.T) {
	row := func() *PixelBuffer {
		pb := NewPixelBuffer(3, 1, 1, 1)
		pb.SetPixel(0, 0, 5, 200, 200, 200)
		pb.SetPixel(1, 0, 5, 200, 200, 200)
		pb.SetPixel(2, 0, 0, 200, 200, 200) // behind by 5
		return pb
	}

	pb := row()
	pb.Outline(1, 0.35)
	want := []uint8{200, 70, 200}
	for i, w := range want {
		if got := pb.Pixels[i].R; got != w {
			t.Errorf("step 1: pixel %d R = %d, want %d", i, got, w)
		}
	}

	pb = row()
	pb.Outline(10, 0.35)
	for i := range 3 {
		if got := pb.Pixels[i].R; got != 200 {
			t.Errorf("step 10: pixel %d R = %d, want 200", i, got)
		}
	}
}

func TestPerspectiveEnlargesNearAtom(t *testing.T) {
	atoms := []pdb.Atom{
		{Serial: 1, Element: "C", Z: 5},  // near
		{Serial: 2, Element: "C", Z: -5}, // far
	}
	pb := NewPixelBuffer(40, 20, 1, 2)

	pts, _ := projectAtomsPixel(atoms, pb, SceneConfig{Zoom: 20, CameraDist: 30})
	if pts[0].Radius <= pts[1].Radius {
		t.Errorf("CameraDist 30: near radius %d, far radius %d, want near larger", pts[0].Radius, pts[1].Radius)
	}

	pts, _ = projectAtomsPixel(atoms, pb, SceneConfig{Zoom: 20})
	if pts[0].Radius != pts[1].Radius {
		t.Errorf("CameraDist 0: radii %d and %d, want equal", pts[0].Radius, pts[1].Radius)
	}
}

func TestPerspectiveClamps(t *testing.T) {
	cfg := SceneConfig{CameraDist: 10}
	if got := perspective(cfg, 10); got != perspectiveMax {
		t.Errorf("at the camera: %v, want %v", got, perspectiveMax)
	}
	if got := perspective(cfg, -1000); got != perspectiveMin {
		t.Errorf("far away: %v, want %v", got, perspectiveMin)
	}
	if got := perspective(SceneConfig{}, 7); got != 1 {
		t.Errorf("orthographic: %v, want 1", got)
	}
}

func TestSpecularBrightensDiskCentre(t *testing.T) {
	pb := NewPixelBuffer(20, 10, 1, 2)
	drawDiskPixel(pb, 10, 10, 0, "#808080", 4)
	centre := pb.Pixels[10*pb.PWidth+10]

	// Lambert alone at the centre, where the normal is (0, 0, 1).
	lambert := 0.7 / math.Sqrt(0.4*0.4+0.6*0.6+0.7*0.7)
	diffuse := uint8(128 * lambert)
	if centre.R < diffuse+1 {
		t.Fatalf("centre R = %d, want above Lambert-only %d", centre.R, diffuse)
	}
}
