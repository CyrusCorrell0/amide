package render

import (
	"math"
	"sort"

	"github.com/CyrusCorrell0/amide/tui/pdb"
	"github.com/charmbracelet/lipgloss"
)

var (
	ColorCartoonCoil   = lipgloss.Color("#909090") // gray
	ColorCartoonHelix  = lipgloss.Color("#A020F0") // purple
	ColorCartoonStrand = lipgloss.Color("#FFC832") // gold
)

type caAtom struct {
	Atom  pdb.Atom
	SX    int
	SY    int
	Depth float64
	SS    pdb.SSType
}

// RenderCartoonPixel draws a cartoon secondary-structure ribbon into a PixelBuffer.
func RenderCartoonPixel(pbuf *PixelBuffer, frame pdb.Frame, segs []pdb.SSSegment, cfg SceneConfig) {
	pbuf.Clear()
	chains := buildCATracePixel(frame, segs, pbuf, cfg)
	for _, cas := range chains {
		renderCartoonPixelImpl(pbuf, cas)
	}
}

// ---- CA trace construction -------------------------------------------------

func buildCATracePixel(frame pdb.Frame, segs []pdb.SSSegment, pbuf *PixelBuffer, cfg SceneConfig) map[byte][]caAtom {
	chains := map[byte][]caAtom{}
	for _, a := range frame.Atoms {
		if a.Name != "CA" {
			continue
		}
		rot := RotateYX(Vec3{
			X: a.X - cfg.CenterX,
			Y: a.Y - cfg.CenterY,
			Z: a.Z - cfg.CenterZ,
		}, cfg.RotY, cfg.RotX)
		sx, sy := projectPixel(pbuf, rot, cfg)
		chains[a.ChainID] = append(chains[a.ChainID], caAtom{
			Atom:  a,
			SX:    sx,
			SY:    sy,
			Depth: rot.Z,
			SS:    pdb.SSTypeFor(segs, a.ChainID, a.ResSeq),
		})
	}
	sortChains(chains)
	return chains
}

func sortChains(chains map[byte][]caAtom) {
	for k := range chains {
		sort.Slice(chains[k], func(i, j int) bool {
			return chains[k][i].Atom.ResSeq < chains[k][j].Atom.ResSeq
		})
	}
}

// caGap returns true if two consecutive CA atoms are too far apart to be bonded
// (missing residues, chain break). 10 Å threshold.
func caGap(a, b caAtom) bool {
	dx := a.Atom.X - b.Atom.X
	dy := a.Atom.Y - b.Atom.Y
	dz := a.Atom.Z - b.Atom.Z
	return dx*dx+dy*dy+dz*dz > 100
}

// ---- Pixel cartoon rendering -----------------------------------------------

func renderCartoonPixelImpl(pbuf *PixelBuffer, cas []caAtom) {
	n := len(cas)
	for i := 0; i < n-1; i++ {
		a, b := cas[i], cas[i+1]
		if caGap(a, b) {
			continue
		}
		r, g, bv := LipglossToRGB(cartoonColor(a.SS))
		isLastInStrand := a.SS == pdb.SSStrand &&
			(i == n-2 || cas[i+1].SS != pdb.SSStrand)

		switch a.SS {
		case pdb.SSHelix:
			drawHelixBondPixel(pbuf, a, b, r, g, bv)
		case pdb.SSStrand:
			drawStrandBondPixel(pbuf, a, b, r, g, bv, isLastInStrand)
		default:
			drawLinePixel(pbuf, a.SX, a.SY, a.Depth, b.SX, b.SY, b.Depth, r, g, bv)
		}
	}
}

// drawHelixBondPixel draws a 5-unit-wide ribbon for an alpha-helix bond.
func drawHelixBondPixel(pbuf *PixelBuffer, a, b caAtom, r, g, bv uint8) {
	dx := float64(b.SX - a.SX)
	dy := float64(b.SY - a.SY)
	length := math.Sqrt(dx*dx + dy*dy)
	if length < 0.5 {
		drawLinePixel(pbuf, a.SX, a.SY, a.Depth, b.SX, b.SY, b.Depth, r, g, bv)
		return
	}
	px := -dy / length
	py := dx / length

	for offset := -2; offset <= 2; offset++ {
		ox, oy := ribbonOffset(pbuf, px, py, float64(offset))
		drawLinePixel(pbuf, a.SX+ox, a.SY+oy, a.Depth, b.SX+ox, b.SY+oy, b.Depth, r, g, bv)
	}
}

// drawStrandBondPixel draws a 3-unit-wide ribbon + optional triangular arrowhead.
func drawStrandBondPixel(pbuf *PixelBuffer, a, b caAtom, r, g, bv uint8, isArrowHead bool) {
	dx := float64(b.SX - a.SX)
	dy := float64(b.SY - a.SY)
	length := math.Sqrt(dx*dx + dy*dy)
	if length < 0.5 {
		drawLinePixel(pbuf, a.SX, a.SY, a.Depth, b.SX, b.SY, b.Depth, r, g, bv)
		return
	}
	px := -dy / length
	py := dx / length

	for offset := -1; offset <= 1; offset++ {
		ox, oy := ribbonOffset(pbuf, px, py, float64(offset))
		drawLinePixel(pbuf, a.SX+ox, a.SY+oy, a.Depth, b.SX+ox, b.SY+oy, b.Depth, r, g, bv)
	}

	if isArrowHead {
		// Triangular arrowhead: two lines converging from a wide base at b to a tip.
		ndx := dx / length
		ndy := dy / length
		tx, ty := ribbonOffset(pbuf, ndx, ndy, 4)
		tipX, tipY := b.SX+tx, b.SY+ty
		wx, wy := ribbonOffset(pbuf, px, py, 3.5)
		drawLinePixel(pbuf, b.SX+wx, b.SY+wy, b.Depth, tipX, tipY, b.Depth, r, g, bv)
		drawLinePixel(pbuf, b.SX-wx, b.SY-wy, b.Depth, tipX, tipY, b.Depth, r, g, bv)
		// Fill the triangle by drawing spans across the strand direction.
		for s := 0; s <= 4; s++ {
			t := float64(s) / 4.0
			sx, sy := ribbonOffset(pbuf, ndx, ndy, float64(s))
			cx, cy := b.SX+sx, b.SY+sy
			w := 3.5 * (1.0 - t)
			for off := -int(math.Ceil(w)); off <= int(math.Ceil(w)); off++ {
				ox, oy := ribbonOffset(pbuf, px, py, float64(off))
				pbuf.SetPixel(cx+ox, cy+oy, b.Depth-0.05, r, g, bv)
			}
		}
	}
}

// ribbonOffset scales a direction (ux, uy) by d scene units and converts it to a
// pixel offset, so ribbon widths look the same on every grid.
func ribbonOffset(pbuf *PixelBuffer, ux, uy, d float64) (int, int) {
	return int(math.Round(ux * d * pbuf.ScaleX())), int(math.Round(uy * d * pbuf.ScaleY()))
}

func cartoonColor(ss pdb.SSType) lipgloss.Color {
	switch ss {
	case pdb.SSHelix:
		return ColorCartoonHelix
	case pdb.SSStrand:
		return ColorCartoonStrand
	default:
		return ColorCartoonCoil
	}
}
