package render

import (
	"math"
	"strings"

	"github.com/CyrusCorrell0/amide/tui/pdb"
	"github.com/charmbracelet/lipgloss"
)

// PixelCell is one pixel of the scene: its colour, its depth (larger is nearer the
// viewer) and whether anything has been drawn there this frame.
type PixelCell struct {
	R, G, B  uint8
	Depth    float64
	HasColor bool
}

// PixelBuffer is the scene rasterised at sub-cell resolution. Each terminal cell holds
// a PerCellX by PerCellY block of pixels, so one buffer type serves every encoder:
// half blocks read 1x2, quadrants 2x2, braille 2x4, and a graphics protocol whatever
// the terminal's cell size is in real pixels.
type PixelBuffer struct {
	TermW, TermH       int         // terminal cells
	PerCellX, PerCellY int         // pixels per cell
	PWidth, PHeight    int         // TermW*PerCellX, TermH*PerCellY
	Pixels             []PixelCell // row-major, index py*PWidth+px
}

func NewPixelBuffer(termW, termH, perCellX, perCellY int) *PixelBuffer {
	termW, termH = max(1, termW), max(1, termH)
	perCellX, perCellY = max(1, perCellX), max(1, perCellY)
	pb := &PixelBuffer{
		TermW:    termW,
		TermH:    termH,
		PerCellX: perCellX,
		PerCellY: perCellY,
		PWidth:   termW * perCellX,
		PHeight:  termH * perCellY,
	}
	pb.Pixels = make([]PixelCell, pb.PWidth*pb.PHeight)
	pb.Clear()
	return pb
}

func (pb *PixelBuffer) Resize(termW, termH, perCellX, perCellY int) {
	if termW == pb.TermW && termH == pb.TermH && perCellX == pb.PerCellX && perCellY == pb.PerCellY {
		return
	}
	*pb = *NewPixelBuffer(termW, termH, perCellX, perCellY)
}

// ScaleX and ScaleY convert scene units to pixels. The scene is laid out in units
// where one unit is one terminal column wide and half a terminal row tall (the
// half-block grid), so zoom and atom size look the same on every grid; a finer grid
// only adds detail.
func (pb *PixelBuffer) ScaleX() float64 { return float64(pb.PerCellX) }
func (pb *PixelBuffer) ScaleY() float64 { return float64(pb.PerCellY) / 2 }

func (pb *PixelBuffer) Clear() {
	for i := range pb.Pixels {
		pb.Pixels[i] = PixelCell{Depth: math.Inf(-1)}
	}
}

func (pb *PixelBuffer) SetPixel(px, py int, depth float64, r, g, b uint8) {
	if px < 0 || px >= pb.PWidth || py < 0 || py >= pb.PHeight {
		return
	}
	idx := py*pb.PWidth + px
	if depth < pb.Pixels[idx].Depth {
		return
	}
	pb.Pixels[idx] = PixelCell{R: r, G: g, B: b, Depth: depth, HasColor: true}
}

// canvas background matches ui.ColorBackground (#000000, entirely black)
const (
	pixelBgR uint8 = 0x00
	pixelBgG uint8 = 0x00
	pixelBgB uint8 = 0x00
)

// RenderHalfBlock serialises the pixel buffer into ANSI true-colour half-block output.
// It assumes a 1x2 grid (PerCellX 1, PerCellY 2): the top pixel is the foreground of
// ▀ and the bottom pixel the background. FG+BG colour pairs are run-length
// compressed: a new escape is only emitted when the pair changes, reducing output by
// ~10× over per-cell wrapping.
func (pb *PixelBuffer) RenderHalfBlock() string {
	var sb strings.Builder
	sb.Grow((pb.TermW*5 + 1) * pb.TermH)

	var cFgR, cFgG, cFgBv, cBgR, cBgG, cBgBv uint8
	fresh := true

	for row := 0; row < pb.TermH; row++ {
		fresh = true
		topBase := (row * 2) * pb.PWidth
		botBase := (row*2 + 1) * pb.PWidth
		for col := 0; col < pb.TermW; col++ {
			top := pb.Pixels[topBase+col]
			bot := pb.Pixels[botBase+col]

			var fgR, fgG, fgBv, bgR, bgG, bgBv uint8
			var ch string

			switch {
			case top.HasColor && bot.HasColor:
				fgR, fgG, fgBv = top.R, top.G, top.B
				bgR, bgG, bgBv = bot.R, bot.G, bot.B
				ch = "▀"
			case top.HasColor:
				fgR, fgG, fgBv = top.R, top.G, top.B
				bgR, bgG, bgBv = pixelBgR, pixelBgG, pixelBgB
				ch = "▀"
			case bot.HasColor:
				fgR, fgG, fgBv = bot.R, bot.G, bot.B
				bgR, bgG, bgBv = pixelBgR, pixelBgG, pixelBgB
				ch = "▄"
			default:
				fgR, fgG, fgBv = pixelBgR, pixelBgG, pixelBgB
				bgR, bgG, bgBv = pixelBgR, pixelBgG, pixelBgB
				ch = " "
			}

			if fresh || fgR != cFgR || fgG != cFgG || fgBv != cFgBv ||
				bgR != cBgR || bgG != cBgG || bgBv != cBgBv {
				writeAnsiRGBPair(&sb, fgR, fgG, fgBv, bgR, bgG, bgBv)
				cFgR, cFgG, cFgBv = fgR, fgG, fgBv
				cBgR, cBgG, cBgBv = bgR, bgG, bgBv
				fresh = false
			}
			sb.WriteString(ch)
		}
		sb.WriteString("\x1b[0m")
		if row < pb.TermH-1 {
			sb.WriteByte('\n')
		}
	}
	return sb.String()
}

// writeAnsiRGBPair writes a combined FG + BG true-colour escape sequence.
// Using strconv-style integer formatting avoids fmt reflection overhead.
func writeAnsiRGBPair(sb *strings.Builder, fr, fg, fb, br, bg, bb uint8) {
	sb.WriteString("\x1b[38;2;")
	writeByteDecimal(sb, fr)
	sb.WriteByte(';')
	writeByteDecimal(sb, fg)
	sb.WriteByte(';')
	writeByteDecimal(sb, fb)
	sb.WriteString("m\x1b[48;2;")
	writeByteDecimal(sb, br)
	sb.WriteByte(';')
	writeByteDecimal(sb, bg)
	sb.WriteByte(';')
	writeByteDecimal(sb, bb)
	sb.WriteByte('m')
}

func writeByteDecimal(sb *strings.Builder, v uint8) {
	if v >= 100 {
		sb.WriteByte('0' + v/100)
		v %= 100
		sb.WriteByte('0' + v/10)
		v %= 10
	} else if v >= 10 {
		sb.WriteByte('0' + v/10)
		v %= 10
	}
	sb.WriteByte('0' + v)
}

// ParseHexColor converts a "#RRGGBB" or "#RGB" hex string to r, g, b bytes.
func ParseHexColor(hex string) (r, g, b uint8) {
	s := strings.TrimPrefix(hex, "#")
	if len(s) == 3 {
		s = string([]byte{s[0], s[0], s[1], s[1], s[2], s[2]})
	}
	if len(s) < 6 {
		return 0x90, 0x90, 0x90
	}
	u := func(c byte) uint8 {
		switch {
		case c >= '0' && c <= '9':
			return c - '0'
		case c >= 'a' && c <= 'f':
			return c - 'a' + 10
		case c >= 'A' && c <= 'F':
			return c - 'A' + 10
		}
		return 0
	}
	r = u(s[0])<<4 | u(s[1])
	g = u(s[2])<<4 | u(s[3])
	b = u(s[4])<<4 | u(s[5])
	return
}

// LipglossToRGB converts a lipgloss.Color hex string to r, g, b bytes.
func LipglossToRGB(c lipgloss.Color) (uint8, uint8, uint8) {
	return ParseHexColor(string(c))
}

// ---- Pixel-space drawing primitives ----------------------------------------

// drawLinePixel draws a Bresenham line into the PixelBuffer.
func drawLinePixel(pbuf *PixelBuffer, x0, y0 int, z0 float64, x1, y1 int, z1 float64, r, g, b uint8) {
	dx := abs(x1 - x0)
	dy := -abs(y1 - y0)
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
		pbuf.SetPixel(x0, y0, z, r, g, b)
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

const (
	// specularStrength is the peak highlight as a fraction of white.
	specularStrength = 0.6
	// specularPower is the Blinn-Phong exponent; higher gives a tighter highlight.
	specularPower = 24
)

// shade lights one channel: diffuse c*lambert plus the specular add, clamped at 255.
func shade(c uint8, lambert, spec float64) uint8 {
	return uint8(min(255, float64(c)*lambert+spec))
}

// drawDiskPixel rasterises a shaded sphere into the PixelBuffer. radius is in scene
// units; on grids whose pixels are not square in scene units the sphere becomes an
// ellipse in pixels so it stays round on screen.
func drawDiskPixel(pbuf *PixelBuffer, cx, cy int, depth float64, color lipgloss.Color, radius int) {
	radius = max(1, radius)
	rx := max(1, int(math.Round(float64(radius)*pbuf.ScaleX())))
	ry := max(1, int(math.Round(float64(radius)*pbuf.ScaleY())))
	cr, cg, cb := LipglossToRGB(color)
	light := Vec3{X: 0.4, Y: -0.6, Z: 0.7}
	ll := math.Sqrt(light.X*light.X + light.Y*light.Y + light.Z*light.Z)
	light.X /= ll
	light.Y /= ll
	light.Z /= ll
	// Blinn-Phong half vector between the light and the viewer at (0, 0, 1).
	half := Vec3{X: light.X, Y: light.Y, Z: light.Z + 1}
	hl := math.Sqrt(half.X*half.X + half.Y*half.Y + half.Z*half.Z)
	half.X /= hl
	half.Y /= hl
	half.Z /= hl

	// Integer inside test: (dx/rx)^2 + (dy/ry)^2 <= 1 without rounding at the rim.
	rx2, ry2 := rx*rx, ry*ry
	for dy := -ry; dy <= ry; dy++ {
		for dx := -rx; dx <= rx; dx++ {
			if dx*dx*ry2+dy*dy*rx2 > rx2*ry2 {
				continue
			}
			nx := float64(dx) / float64(rx)
			ny := float64(dy) / float64(ry)
			nz2 := 1.0 - nx*nx - ny*ny
			if nz2 < 0 {
				continue
			}
			nz := math.Sqrt(nz2)
			lambert := maxFloat(0.15, nx*light.X+ny*light.Y+nz*light.Z)
			spec := 255 * specularStrength * math.Pow(maxFloat(0, nx*half.X+ny*half.Y+nz*half.Z), specularPower)
			z := depth + nz*0.35
			pbuf.SetPixel(cx+dx, cy+dy, z,
				shade(cr, lambert, spec),
				shade(cg, lambert, spec),
				shade(cb, lambert, spec),
			)
		}
	}
}

// ---- Pixel-space atom projection + frame render ----------------------------

type projectedAtomPx struct {
	Serial int
	SX, SY int
	Depth  float64
	Color  lipgloss.Color
	Radius int // scene units; drawDiskPixel scales it per axis
}

// projectAtomsPixel projects atoms into PixelBuffer pixel coordinates. Positions are
// computed in scene units and scaled to the buffer's grid; PanY is in terminal rows,
// which is two scene units.
func projectAtomsPixel(atoms []pdb.Atom, pbuf *PixelBuffer, cfg SceneConfig) ([]projectedAtomPx, map[int]projectedAtomPx) {
	out := make([]projectedAtomPx, 0, len(atoms))
	bySerial := make(map[int]projectedAtomPx, len(atoms))
	scale := maxFloat(0.5, cfg.Zoom*0.16)

	for i, a := range atoms {
		if !cfg.ShowHydrogen && strings.EqualFold(a.Element, "H") {
			continue
		}
		rot := RotateYX(Vec3{
			X: a.X - cfg.CenterX,
			Y: a.Y - cfg.CenterY,
			Z: a.Z - cfg.CenterZ,
		}, cfg.RotY, cfg.RotX)

		px, py := projectPixel(pbuf, rot, cfg)

		radius := int(math.Round(VdWRadius(a.Element) * scale * perspective(cfg, rot.Z)))
		radius = max(2, min(radius, 12))

		serial := a.Serial
		if serial == 0 {
			serial = i + 1
		}
		p := projectedAtomPx{
			Serial: serial,
			SX:     px,
			SY:     py,
			Depth:  rot.Z,
			Color:  ElementColor(a.Element),
			Radius: radius,
		}
		out = append(out, p)
		bySerial[p.Serial] = p
	}
	return out, bySerial
}

// projectPixel maps a rotated scene point to the nearest pixel of pbuf, with the
// perspective factor applied when cfg.CameraDist is set.
func projectPixel(pbuf *PixelBuffer, rot Vec3, cfg SceneConfig) (int, int) {
	sx, sy := pbuf.ScaleX(), pbuf.ScaleY()
	s := perspective(cfg, rot.Z)
	px := float64(pbuf.PWidth)/2.0 + rot.X*s*cfg.Zoom*sx + cfg.PanX*sx
	py := float64(pbuf.PHeight)/2.0 - rot.Y*s*cfg.Zoom*sy + cfg.PanY*2*sy
	return int(math.Round(px)), int(math.Round(py))
}

func renderWireframePixel(pbuf *PixelBuffer, bonds []pdb.Bond, bySerial map[int]projectedAtomPx) {
	const lr, lg, lb uint8 = 0x8B, 0x94, 0x9E // #8B949E
	for _, b := range bonds {
		a, okA := bySerial[b.A]
		c, okB := bySerial[b.B]
		if !okA || !okB {
			continue
		}
		drawLinePixel(pbuf, a.SX, a.SY, a.Depth, c.SX, c.SY, c.Depth, lr, lg, lb)
	}
}

// RenderFramePixel renders one PDB frame as atoms and bonds into a PixelBuffer.
func RenderFramePixel(pbuf *PixelBuffer, frame pdb.Frame, mode RenderMode, cfg SceneConfig) {
	pbuf.Clear()
	if len(frame.Atoms) == 0 {
		return
	}
	pts, bySerial := projectAtomsPixel(frame.Atoms, pbuf, cfg)
	switch mode {
	case ModeWireframe:
		renderWireframePixel(pbuf, frame.Bonds, bySerial)
	case ModeBallStick:
		renderWireframePixel(pbuf, frame.Bonds, bySerial)
		for _, p := range pts {
			drawDiskPixel(pbuf, p.SX, p.SY, p.Depth, p.Color, max(2, p.Radius/2))
		}
	default:
		for _, p := range pts {
			drawDiskPixel(pbuf, p.SX, p.SY, p.Depth, p.Color, p.Radius)
		}
	}
}
