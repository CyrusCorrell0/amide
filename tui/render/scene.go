package render

import "github.com/CyrusCorrell0/amide/tui/pdb"

const (
	// depthCueFloor is the brightness left on the farthest pixel; the nearest keeps full colour.
	depthCueFloor = 0.35
	// outlineStep is the depth gap in angstrom between neighbours that earns an edge.
	outlineStep = 1.0
	// outlineDarken scales the colour of an edge pixel.
	outlineDarken = 0.35
)

// RenderScene draws one frame into pb the way the viewer shows it. It is the single
// place that decides between the cartoon and the atom renderers and applies the depth
// cues, so the viewer and the GIF recorder can never disagree.
func RenderScene(pb *PixelBuffer, frame pdb.Frame, segs []pdb.SSSegment, mode RenderMode, showCartoon bool, cfg SceneConfig) {
	if showCartoon || mode == ModeCartoon {
		RenderCartoonPixel(pb, frame, segs, cfg)
	} else {
		RenderFramePixel(pb, frame, mode, cfg)
	}
	pb.DepthCue(depthCueFloor)
	pb.Outline(outlineStep, outlineDarken)
}
