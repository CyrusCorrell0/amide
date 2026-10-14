package render

type RenderMode int

const (
	ModeSpaceFill RenderMode = iota
	ModeBallStick
	ModeWireframe
	// ModeCartoon renders a secondary-structure ribbon: alpha-helices as a wide
	// purple ribbon, beta-sheets as gold arrows, and coils/loops as a thin gray
	// trace. It reads HELIX/SHEET records from the PDB.
	ModeCartoon

	// ModeCount is the number of selectable render modes (used to cycle).
	ModeCount = iota
)

type SceneConfig struct {
	CenterX, CenterY, CenterZ float64
	RotX, RotY                float64
	Zoom                      float64
	PanX, PanY                float64
	ShowHydrogen              bool
	CameraDist                float64 // distance from the camera to the molecule centre in angstrom; 0 is orthographic
}

func abs(v int) int {
	if v < 0 {
		return -v
	}
	return v
}

func maxFloat(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}
