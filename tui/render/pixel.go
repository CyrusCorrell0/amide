package render

import (
	"math"
	"strings"

	"github.com/CyrusCorrell0/amide/tui/pdb"
	"github.com/charmbracelet/lipgloss"
)

// PixelBuffer provides 2× vertical resolution by using Unicode half-block characters.
// Each terminal cell represents two pixels: the top pixel sets the foreground colour
// (rendered with ▀) and the bottom pixel sets the background colour.
type PixelCell struct {
	R, G, B  uint8
	Depth    float64
	HasColor bool
}

type PixelBuffer struct {
	TermW   int // terminal columns
	TermH   int // terminal rows
	PWidth  int // pixel columns  = TermW
	PHeight int // pixel rows     = TermH * 2
	Pixels  []PixelCell
}

func NewPixelBuffer(termW, termH int) *PixelBuffer {
	if termW < 1 {
		termW = 1
	}
	if termH < 1 {
		termH = 1
	}
	pb := &PixelBuffer{
		TermW:   termW,
		TermH:   termH,
		PWidth:  termW,
		PHeight: termH * 2,
	}
	pb.Pixels = make([]PixelCell, pb.PWidth*pb.PHeight)
	pb.Clear()
	return pb
}

func (pb *PixelBuffer) Resize(termW, termH int) {
	if termW == pb.TermW && termH == pb.TermH {
		return
	}
	*pb = *NewPixelBuffer(termW, termH)
}

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

// Render serialises the pixel buffer into ANSI true-colour half-block output.
// FG+BG colour pairs are run-length compressed: a new escape is only emitted when
// the pair changes, reducing output by ~10× over per-cell wrapping.
func (pb *PixelBuffer) Render() string {
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

// drawDiskPixel rasterises a shaded sphere disk into the PixelBuffer.
func drawDiskPixel(pbuf *PixelBuffer, cx, cy int, depth float64, color lipgloss.Color, radius int) {
	if radius < 1 {
		radius = 1
	}
	cr, cg, cb := LipglossToRGB(color)
	light := Vec3{X: 0.4, Y: -0.6, Z: 0.7}
	ll := math.Sqrt(light.X*light.X + light.Y*light.Y + light.Z*light.Z)
	light.X /= ll
	light.Y /= ll
	light.Z /= ll

	r2 := radius * radius
	for dy := -radius; dy <= radius; dy++ {
		for dx := -radius; dx <= radius; dx++ {
			if dx*dx+dy*dy > r2 {
				continue
			}
			nx := float64(dx) / float64(radius)
			ny := float64(dy) / float64(radius)
			nz2 := 1.0 - nx*nx - ny*ny
			if nz2 < 0 {
				continue
			}
			nz := math.Sqrt(nz2)
			lambert := maxFloat(0.15, nx*light.X+ny*light.Y+nz*light.Z)
			z := depth + nz*0.35
			pbuf.SetPixel(cx+dx, cy+dy, z,
				uint8(float64(cr)*lambert),
				uint8(float64(cg)*lambert),
				uint8(float64(cb)*lambert),
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
	Radius int
}

// projectAtomsPixel projects atoms into PixelBuffer pixel coordinates.
// Y projection uses factor 1.0 (not 0.5) because each pixel row ≈ half a terminal row,
// giving the same apparent scale as the ASCII projection with its 0.5 correction.
func projectAtomsPixel(atoms []pdb.Atom, pWidth, pHeight int, cfg SceneConfig) ([]projectedAtomPx, map[int]projectedAtomPx) {
	out := make([]projectedAtomPx, 0, len(atoms))
	bySerial := make(map[int]projectedAtomPx, len(atoms))
	scale := maxFloat(0.5, cfg.Zoom*0.16) // 2× the ASCII 0.08 factor

	for i, a := range atoms {
		if !cfg.ShowHydrogen && strings.EqualFold(a.Element, "H") {
			continue
		}
		rot := RotateYX(Vec3{
			X: a.X - cfg.CenterX,
			Y: a.Y - cfg.CenterY,
			Z: a.Z - cfg.CenterZ,
		}, cfg.RotY, cfg.RotX)

		px := float64(pWidth)/2.0 + rot.X*cfg.Zoom + cfg.PanX
		py := float64(pHeight)/2.0 - rot.Y*cfg.Zoom + cfg.PanY*2

		radius := int(math.Round(VdWRadius(a.Element) * scale))
		radius = max(2, min(radius, 12))

		serial := a.Serial
		if serial == 0 {
			serial = i + 1
		}
		p := projectedAtomPx{
			Serial: serial,
			SX:     int(math.Round(px)),
			SY:     int(math.Round(py)),
			Depth:  rot.Z,
			Color:  ElementColor(a.Element),
			Radius: radius,
		}
		out = append(out, p)
		bySerial[p.Serial] = p
	}
	return out, bySerial
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

// RenderFramePixel renders one PDB frame into a PixelBuffer using half-block pixel art.
func RenderFramePixel(pbuf *PixelBuffer, frame pdb.Frame, mode RenderMode, cfg SceneConfig) {
	pbuf.Clear()
	if len(frame.Atoms) == 0 {
		return
	}
	pts, bySerial := projectAtomsPixel(frame.Atoms, pbuf.PWidth, pbuf.PHeight, cfg)
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
