package render

import (
	"math"
	"strings"

	"github.com/CyrusCorrell0/amide/tui/pdb"
	"github.com/charmbracelet/lipgloss"
)

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
}

type projectedAtom struct {
	Serial  int
	SX, SY  int
	Depth   float64
	Element string
	Color   lipgloss.Color
	Radius  int
}

func RenderFrame(buf *ScreenBuffer, frame pdb.Frame, mode RenderMode, cfg SceneConfig) {
	buf.Clear()
	if len(frame.Atoms) == 0 {
		return
	}

	pts, bySerial := projectAtoms(frame.Atoms, buf.Width, buf.Height, cfg)
	switch mode {
	case ModeWireframe:
		renderWireframe(buf, frame.Bonds, bySerial)
	case ModeBallStick:
		renderWireframe(buf, frame.Bonds, bySerial)
		for _, p := range pts {
			drawDisk(buf, p.SX, p.SY, p.Depth, p.Color, max(1, p.Radius/2), true)
		}
	default:
		for _, p := range pts {
			drawDisk(buf, p.SX, p.SY, p.Depth, p.Color, p.Radius, true)
		}
	}
}

func projectAtoms(atoms []pdb.Atom, canvasW, canvasH int, cfg SceneConfig) ([]projectedAtom, map[int]projectedAtom) {
	out := make([]projectedAtom, 0, len(atoms))
	bySerial := make(map[int]projectedAtom, len(atoms))
	scale := math.Max(0.5, cfg.Zoom*0.08)

	for i, a := range atoms {
		if !cfg.ShowHydrogen && strings.EqualFold(a.Element, "H") {
			continue
		}
		rot := RotateYX(Vec3{
			X: a.X - cfg.CenterX,
			Y: a.Y - cfg.CenterY,
			Z: a.Z - cfg.CenterZ,
		}, cfg.RotY, cfg.RotX)
		sx, sy := Project(rot, canvasW, canvasH, cfg.Zoom, cfg.PanX, cfg.PanY)
		radius := int(math.Round(VdWRadius(a.Element) * scale))
		radius = max(1, min(radius, 6))
		serial := a.Serial
		if serial == 0 {
			serial = i + 1
		}
		p := projectedAtom{
			Serial:  serial,
			SX:      sx,
			SY:      sy,
			Depth:   rot.Z,
			Element: a.Element,
			Color:   ElementColor(a.Element),
			Radius:  radius,
		}
		out = append(out, p)
		bySerial[p.Serial] = p
	}
	return out, bySerial
}

func renderWireframe(buf *ScreenBuffer, bonds []pdb.Bond, bySerial map[int]projectedAtom) {
	lineColor := lipgloss.Color("#8B949E")
	for _, b := range bonds {
		a, okA := bySerial[b.A]
		c, okB := bySerial[b.B]
		if !okA || !okB {
			continue
		}
		drawLine(buf, a.SX, a.SY, a.Depth, c.SX, c.SY, c.Depth, lineColor)
	}
}

func drawDisk(buf *ScreenBuffer, cx, cy int, depth float64, color lipgloss.Color, radius int, shaded bool) {
	if radius < 1 {
		radius = 1
	}
	light := Vec3{X: 0.4, Y: -0.6, Z: 0.7}
	lightLen := math.Sqrt(light.X*light.X + light.Y*light.Y + light.Z*light.Z)
	light.X, light.Y, light.Z = light.X/lightLen, light.Y/lightLen, light.Z/lightLen

	r2 := radius * radius
	for dy := -radius; dy <= radius; dy++ {
		for dx := -radius; dx <= radius; dx++ {
			d := dx*dx + dy*dy
			if d > r2 {
				continue
			}
			nx := float64(dx) / float64(radius)
			ny := float64(dy) / float64(radius)
			nz2 := 1.0 - nx*nx - ny*ny
			if nz2 < 0 {
				continue
			}
			nz := math.Sqrt(nz2)
			lambert := 0.7
			ch := '@'
			if shaded {
				lambert = maxFloat(0.15, nx*light.X+ny*light.Y+nz*light.Z)
				ch = ShadeRune(lambert)
			}
			z := depth + nz*0.35
			buf.Set(cx+dx, cy+dy, z, ch, color)
		}
	}
}

func drawLine(buf *ScreenBuffer, x0, y0 int, z0 float64, x1, y1 int, z1 float64, color lipgloss.Color) {
	dx := int(math.Abs(float64(x1 - x0)))
	dy := -int(math.Abs(float64(y1 - y0)))
	sx := -1
	if x0 < x1 {
		sx = 1
	}
	sy := -1
	if y0 < y1 {
		sy = 1
	}

	err := dx + dy
	steps := max(1, max(abs(x1-x0), abs(y1-y0)))
	step := 0
	for {
		t := float64(step) / float64(steps)
		z := z0*(1.0-t) + z1*t
		buf.Set(x0, y0, z, '·', color)
		if x0 == x1 && y0 == y1 {
			break
		}
		e2 := 2 * err
		if e2 >= dy {
			err += dy
			x0 += sx
		}
		if e2 <= dx {
			err += dx
			y0 += sy
		}
		step++
	}
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
