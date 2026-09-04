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

// RenderCartoon draws a cartoon secondary-structure ribbon into an ASCII ScreenBuffer.
func RenderCartoon(buf *ScreenBuffer, frame pdb.Frame, segs []pdb.SSSegment, cfg SceneConfig) {
	buf.Clear()
	chains := buildCATrace(frame, segs, buf.Width, buf.Height, cfg)
	for _, cas := range chains {
		renderCartoonASCII(buf, cas)
	}
}

// RenderCartoonPixel draws a cartoon secondary-structure ribbon into a PixelBuffer.
func RenderCartoonPixel(pbuf *PixelBuffer, frame pdb.Frame, segs []pdb.SSSegment, cfg SceneConfig) {
	pbuf.Clear()
	chains := buildCATracePixel(frame, segs, pbuf.PWidth, pbuf.PHeight, cfg)
	for _, cas := range chains {
		renderCartoonPixelImpl(pbuf, cas)
	}
}

// ---- CA trace construction -------------------------------------------------

func buildCATrace(frame pdb.Frame, segs []pdb.SSSegment, canvasW, canvasH int, cfg SceneConfig) map[byte][]caAtom {
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
		sx, sy := Project(rot, canvasW, canvasH, cfg.Zoom, cfg.PanX, cfg.PanY)
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

func buildCATracePixel(frame pdb.Frame, segs []pdb.SSSegment, pWidth, pHeight int, cfg SceneConfig) map[byte][]caAtom {
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
		// Pixel Y projection: factor 1.0 (no 0.5); panY converted to pixel rows.
		px := float64(pWidth)/2.0 + rot.X*cfg.Zoom + cfg.PanX
		py := float64(pHeight)/2.0 - rot.Y*cfg.Zoom + cfg.PanY*2
		chains[a.ChainID] = append(chains[a.ChainID], caAtom{
			Atom:  a,
			SX:    int(math.Round(px)),
			SY:    int(math.Round(py)),
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

// ---- ASCII cartoon rendering -----------------------------------------------

func renderCartoonASCII(buf *ScreenBuffer, cas []caAtom) {
	n := len(cas)
	for i := 0; i < n-1; i++ {
		a, b := cas[i], cas[i+1]
		if caGap(a, b) {
			continue
		}
		color := cartoonColor(a.SS)
		isLastInStrand := a.SS == pdb.SSStrand &&
			(i == n-2 || cas[i+1].SS != pdb.SSStrand)

		switch a.SS {
		case pdb.SSHelix:
			drawHelixBondASCII(buf, a, b, color)
		case pdb.SSStrand:
			drawStrandBondASCII(buf, a, b, color, isLastInStrand)
		default:
			drawLine(buf, a.SX, a.SY, a.Depth, b.SX, b.SY, b.Depth, color)
		}
	}
}

// drawHelixBondASCII draws a thick 3-line ribbon to suggest an alpha-helix.
func drawHelixBondASCII(buf *ScreenBuffer, a, b caAtom, color lipgloss.Color) {
	drawLine(buf, a.SX, a.SY, a.Depth, b.SX, b.SY, b.Depth, color)

	dx := float64(b.SX - a.SX)
	dy := float64(b.SY - a.SY)
	length := math.Sqrt(dx*dx + dy*dy)
	if length < 0.5 {
		return
	}
	px := -dy / length
	py := dx / length

	// Offset is scaled to approximate visual square pixels (terminal chars are 2:1).
	ox := int(math.Round(px * 1.5))
	oy := int(math.Round(py * 0.75))
	if ox == 0 && oy == 0 {
		return
	}
	drawLine(buf, a.SX+ox, a.SY+oy, a.Depth-0.05, b.SX+ox, b.SY+oy, b.Depth-0.05, color)
	drawLine(buf, a.SX-ox, a.SY-oy, a.Depth-0.05, b.SX-ox, b.SY-oy, b.Depth-0.05, color)
}

// drawStrandBondASCII draws a 2-line ribbon; at the terminal bond it draws an arrowhead.
func drawStrandBondASCII(buf *ScreenBuffer, a, b caAtom, color lipgloss.Color, isArrowHead bool) {
	drawLine(buf, a.SX, a.SY, a.Depth, b.SX, b.SY, b.Depth, color)

	dx := float64(b.SX - a.SX)
	dy := float64(b.SY - a.SY)
	length := math.Sqrt(dx*dx + dy*dy)
	if length < 0.5 {
		return
	}
	px := -dy / length
	py := dx / length

	ox := int(math.Round(px))
	oy := int(math.Round(py * 0.5))

	if ox != 0 || oy != 0 {
		drawLine(buf, a.SX+ox, a.SY+oy, a.Depth-0.05, b.SX+ox, b.SY+oy, b.Depth-0.05, color)
		drawLine(buf, a.SX-ox, a.SY-oy, a.Depth-0.05, b.SX-ox, b.SY-oy, b.Depth-0.05, color)
	}

	if isArrowHead {
		// Widen the perpendicular spread at the terminus to form an arrowhead.
		wx := int(math.Round(px * 2.5))
		wy := int(math.Round(py * 1.25))
		buf.Set(b.SX+wx, b.SY+wy, b.Depth-0.1, '▶', color)
		buf.Set(b.SX-wx, b.SY-wy, b.Depth-0.1, '◀', color)
		// Tip of the arrowhead one step forward.
		ndx := int(math.Round(dx / length * 2))
		ndy := int(math.Round(dy / length))
		buf.Set(b.SX+ndx, b.SY+ndy, b.Depth-0.08, '▶', color)
	}
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

// drawHelixBondPixel draws a 5-pixel-wide ribbon for an alpha-helix bond.
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
		ox := int(math.Round(px * float64(offset)))
		oy := int(math.Round(py * float64(offset)))
		drawLinePixel(pbuf, a.SX+ox, a.SY+oy, a.Depth, b.SX+ox, b.SY+oy, b.Depth, r, g, bv)
	}
}

// drawStrandBondPixel draws a 3-pixel-wide ribbon + optional triangular arrowhead.
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
		ox := int(math.Round(px * float64(offset)))
		oy := int(math.Round(py * float64(offset)))
		drawLinePixel(pbuf, a.SX+ox, a.SY+oy, a.Depth, b.SX+ox, b.SY+oy, b.Depth, r, g, bv)
	}

	if isArrowHead {
		// Triangular arrowhead: two lines converging from wide base at b to a tip.
		ndx := dx / length
		ndy := dy / length
		tipX := b.SX + int(math.Round(ndx*4))
		tipY := b.SY + int(math.Round(ndy*4))
		leftX := b.SX + int(math.Round(px*3.5))
		leftY := b.SY + int(math.Round(py*3.5))
		rightX := b.SX - int(math.Round(px*3.5))
		rightY := b.SY - int(math.Round(py*3.5))
		drawLinePixel(pbuf, leftX, leftY, b.Depth, tipX, tipY, b.Depth, r, g, bv)
		drawLinePixel(pbuf, rightX, rightY, b.Depth, tipX, tipY, b.Depth, r, g, bv)
		// Fill the triangle by drawing horizontal spans.
		steps := max(1, int(math.Round(4*length/length)))
		for s := 0; s <= 4; s++ {
			t := float64(s) / 4.0
			cx := b.SX + int(math.Round(ndx*float64(s)))
			cy := b.SY + int(math.Round(ndy*float64(s)))
			w := 3.5 * (1.0 - t)
			for off := -int(math.Ceil(w)); off <= int(math.Ceil(w)); off++ {
				ox := int(math.Round(px * float64(off)))
				oy := int(math.Round(py * float64(off)))
				pbuf.SetPixel(cx+ox, cy+oy, b.Depth-0.05, r, g, bv)
			}
		}
		_ = steps
	}
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
