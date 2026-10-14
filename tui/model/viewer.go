package model

import (
	"fmt"
	"image/color"
	"math"
	"path/filepath"
	"strings"
	"time"

	"github.com/CyrusCorrell0/amide/tui/graphics"
	"github.com/CyrusCorrell0/amide/tui/pdb"
	"github.com/CyrusCorrell0/amide/tui/render"
	"github.com/CyrusCorrell0/amide/tui/ui"
	tea "github.com/charmbracelet/bubbletea"
)

type BackToPickerMsg struct{}
type TickMsg struct{}

// RockTickMsg advances the rocking animation by one step.
type RockTickMsg struct{}

// RecordDoneMsg reports the result of an async GIF export.
type RecordDoneMsg struct {
	Path string
	Err  error
}

// The pixel tier's image sits inside the canvas border. AppStyle has no padding
// and CanvasStyle has a one-cell border and one cell of horizontal padding, so the
// interior starts at row 2, column 3 (1-based).
const (
	pixelRow = 2
	pixelCol = 3
	kittyID  = 1
)

// Rocking swings the molecule about its base rotation: about 20 degrees each way,
// one full swing every three seconds at 30 ticks a second.
const (
	rockFPS       = 30
	rockStep      = 2 * math.Pi / 90
	rockAmplitude = 0.35
)

type ViewerModel struct {
	width, height      int
	canvasW, canvasH   int
	sidebarW           int
	mol                *pdb.Molecule
	pbuf               *render.PixelBuffer
	rotX, rotY         float64
	zoom, panX, panY   float64
	frameIdx           int
	playing            bool
	playFPS            int
	mode               render.RenderMode
	canvas             render.Canvas
	gfx                *graphics.Session // nil means text tiers only
	blank              string            // the pixel tier's text: the spaces its image covers
	rocking            bool
	rockBase           float64
	rockPhase          float64
	showH, showSidebar bool
	showCartoon        bool
	dirty              bool
	cachedView         string
	keys               ui.ViewerKeyMap
	srcPath            string
	recording          bool
	recordStatus       string
}

// NewViewerModel opens mol on the quadrant canvas, or on the pixel tier when gfx
// names an image protocol.
func NewViewerModel(width, height int, mol *pdb.Molecule, srcPath string, gfx *graphics.Session) *ViewerModel {
	m := &ViewerModel{
		width:       width,
		height:      height,
		mol:         mol,
		srcPath:     srcPath,
		playFPS:     10,
		showH:       false,
		showSidebar: true,
		mode:        render.ModeBallStick,
		canvas:      render.CanvasQuadrant,
		gfx:         gfx,
		keys:        ui.DefaultViewerKeys(),
		dirty:       true,
	}
	if m.havePixels() {
		m.canvas = render.CanvasPixels
	}
	m.resetView()
	m.resize(width, height)
	return m
}

func (m *ViewerModel) resize(width, height int) {
	m.width = max(1, width)
	m.height = max(1, height)
	m.sidebarW = 28
	if !m.showSidebar {
		m.sidebarW = 0
	}
	m.canvasW = max(1, m.width-m.sidebarW-1)
	m.canvasH = max(1, m.height-4)

	cols, rows := m.canvasCells()
	cellW, cellH := 0, 0
	if m.gfx != nil {
		cellW, cellH = m.gfx.CellW, m.gfx.CellH
	}
	x, y := m.canvas.PerCell(cellW, cellH)
	bufRows := rows
	m.blank = ""
	if m.canvas == render.CanvasPixels {
		if m.gfx.Protocol == graphics.Sixel {
			// A sixel image's height is fixed in pixels, so a cell-size guess that
			// is a little small would spill it over the border. Leave a row spare.
			bufRows = max(1, rows-1)
		}
		line := strings.Repeat(" ", cols)
		m.blank = strings.TrimSuffix(strings.Repeat(line+"\n", rows), "\n")
	}

	if m.pbuf == nil {
		m.pbuf = render.NewPixelBuffer(cols, bufRows, x, y)
	} else {
		m.pbuf.Resize(cols, bufRows, x, y)
	}
	m.dirty = true
}

// canvasCells is the canvas interior in terminal cells.
func (m *ViewerModel) canvasCells() (cols, rows int) {
	return max(1, m.canvasW-4), max(1, m.canvasH-2)
}

func (m *ViewerModel) havePixels() bool {
	return m.gfx != nil && m.gfx.Protocol != graphics.None
}

// leavePixels takes the image off the screen before the pixel tier is left or its
// geometry changes. Kitty keeps a placement until told to delete it; sixel and
// iTerm2 images stay only while the overlay keeps re-sending them.
func (m *ViewerModel) leavePixels() {
	if m.canvas != render.CanvasPixels || m.gfx == nil {
		return
	}
	if m.gfx.Protocol == graphics.Kitty {
		m.gfx.Overlay.Once(pixelRow, pixelCol, graphics.KittyDelete(kittyID))
		return
	}
	m.gfx.Overlay.Clear()
}

func (m *ViewerModel) resetView() {
	m.rotX, m.rotY = 0.3, 0.6
	m.panX, m.panY = 0, 0
	if m.mol == nil {
		m.zoom = 1.0
		return
	}
	if m.mol.Extent == 0 {
		m.zoom = 12.0
		return
	}
	m.zoom = math.Max(2.0, float64(max(10, m.canvasH))/m.mol.Extent)
}

// sceneConfig is the camera the screen and the GIF recorder share. The camera sits
// three molecule extents from the centre, close enough for perspective to read as
// depth without stretching the near side.
func (m *ViewerModel) sceneConfig() render.SceneConfig {
	dist := 30.0
	if m.mol.Extent > 0 {
		dist = 3 * m.mol.Extent
	}
	return render.SceneConfig{
		CenterX:      m.mol.CenterX,
		CenterY:      m.mol.CenterY,
		CenterZ:      m.mol.CenterZ,
		RotX:         m.rotX,
		RotY:         m.rotY,
		Zoom:         m.zoom,
		PanX:         m.panX,
		PanY:         m.panY,
		ShowHydrogen: m.showH,
		CameraDist:   dist,
	}
}

// rotate turns the camera. While rocking, a turn about Y moves the centre of the
// swing, so the user can steer without stopping it.
func (m *ViewerModel) rotate(dx, dy float64) {
	m.rotX += dx
	m.rotY += dy
	if m.rocking {
		m.rockBase += dy
	}
	m.dirty = true
}

func (m *ViewerModel) Update(msg tea.Msg) tea.Cmd {
	const step = 5.0 * math.Pi / 180.0
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.leavePixels()
		m.resize(msg.Width, msg.Height)
		return nil
	case TickMsg:
		if !m.playing || m.mol == nil || len(m.mol.Frames) < 2 {
			return nil
		}
		m.frameIdx = (m.frameIdx + 1) % len(m.mol.Frames)
		m.dirty = true
		return tickCmd(m.playFPS)
	case RockTickMsg:
		if !m.rocking {
			return nil
		}
		m.rockPhase += rockStep
		m.rotY = m.rockBase + rockAmplitude*math.Sin(m.rockPhase)
		m.dirty = true
		return rockTickCmd()
	case RecordDoneMsg:
		m.recording = false
		if msg.Err != nil {
			m.recordStatus = "Record failed: " + msg.Err.Error()
		} else {
			m.recordStatus = "Saved " + filepath.Base(msg.Path)
		}
		return nil
	case tea.KeyMsg:
		switch {
		case msg.String() == "ctrl+c":
			return tea.Quit
		case msg.String() == "q" || msg.String() == "esc":
			m.playing = false
			m.rocking = false
			m.leavePixels()
			return func() tea.Msg { return BackToPickerMsg{} }
		case msg.String() == " ":
			if m.mol != nil && len(m.mol.Frames) > 1 {
				m.playing = !m.playing
				m.dirty = true
				if m.playing {
					return tickCmd(m.playFPS)
				}
			}
		case msg.String() == ".":
			m.advanceFrame(1)
		case msg.String() == ",":
			m.advanceFrame(-1)
		case msg.String() == "[":
			m.playFPS = max(1, m.playFPS-1)
			m.dirty = true
		case msg.String() == "]":
			m.playFPS = min(60, m.playFPS+1)
			m.dirty = true
		case msg.String() == "R":
			return m.startRecording()
		case msg.String() == "r":
			m.rocking = !m.rocking
			m.dirty = true
			if m.rocking {
				m.rockBase, m.rockPhase = m.rotY, 0
				return rockTickCmd()
			}
		case msg.String() == "p":
			m.leavePixels()
			m.canvas = m.canvas.Next(m.havePixels())
			m.resize(m.width, m.height)
		case msg.String() == "m":
			m.mode = (m.mode + 1) % render.ModeCount
			m.dirty = true
		case msg.String() == "c":
			m.showCartoon = !m.showCartoon
			m.dirty = true
		case msg.String() == "e":
			m.showH = !m.showH
			m.dirty = true
		case msg.String() == "tab":
			m.showSidebar = !m.showSidebar
			m.leavePixels()
			m.resize(m.width, m.height)
		case msg.String() == "0":
			m.resetView()
			m.rockBase, m.rockPhase = m.rotY, 0
			m.dirty = true
		case msg.String() == "+" || msg.String() == "=":
			m.zoom *= 1.15
			m.dirty = true
		case msg.String() == "-" || msg.String() == "_":
			m.zoom = math.Max(0.5, m.zoom/1.15)
			m.dirty = true
		case msg.String() == "left" || msg.String() == "h" || msg.String() == "a":
			m.rotate(0, -step)
		case msg.String() == "right" || msg.String() == "l" || msg.String() == "d":
			m.rotate(0, step)
		case msg.String() == "up" || msg.String() == "k" || msg.String() == "w":
			m.rotate(-step, 0)
		case msg.String() == "down" || msg.String() == "j" || msg.String() == "s":
			m.rotate(step, 0)
		case msg.String() == "H":
			m.panX -= 2
			m.dirty = true
		case msg.String() == "L":
			m.panX += 2
			m.dirty = true
		case msg.String() == "K":
			m.panY -= 1
			m.dirty = true
		case msg.String() == "J":
			m.panY += 1
			m.dirty = true
		}
	}
	return nil
}

func (m *ViewerModel) View() string {
	if m.width < 10 || m.height < 5 {
		return ui.ErrorStyle.Render("Terminal too small")
	}
	if m.mol == nil || len(m.mol.Frames) == 0 {
		return ui.ErrorStyle.Render("No molecule loaded")
	}

	if m.dirty {
		m.draw()
		m.dirty = false
	}

	canvas := ui.CanvasStyle.
		Width(max(1, m.canvasW-4)).
		Height(max(1, m.canvasH-2)).
		Render(m.cachedView)

	top := canvas
	if m.showSidebar {
		sidebar := ui.SidebarStyle.
			Width(max(10, m.sidebarW-4)).
			Height(max(1, m.canvasH-2)).
			Render(m.sidebarView())
		top = joinHorizontalTop(canvas, sidebar)
	}

	// The second line names every scene parameter. Bubble Tea skips a flush when
	// the view string is unchanged, and the pixel tier's text is blank, so without
	// it a rotation there would never reach the terminal.
	bar := ui.StatusBarStyle.Width(max(1, m.width-2)).MaxHeight(1) // a wrapped line would push the canvas off screen
	status := bar.Render(
		"[hjkl] rotate  [HJKL] pan  [+/-] zoom  [space] play  [.,] frame  [m] mode  [p] canvas  [r] rock  [q] back",
	) + "\n" + bar.Render(m.stateLine())

	if m.recordStatus != "" {
		status += "\n" + bar.Render(m.recordStatus)
	}

	return ui.AppStyle.Width(max(1, m.width)).Height(max(1, m.height)).Render(top + "\n" + status)
}

// draw renders the scene into the buffer and sets cachedView. A text tier encodes
// the buffer as glyphs. The pixel tier shows blank cells and hands the image to the
// overlay; if the image cannot be encoded the viewer falls back to quadrants and
// says why.
func (m *ViewerModel) draw() {
	render.RenderScene(m.pbuf, m.mol.Frames[m.frameIdx], m.mol.SSSegments, m.mode, m.showCartoon, m.sceneConfig())
	if m.canvas != render.CanvasPixels {
		m.cachedView = m.canvas.Encode(m.pbuf)
		return
	}
	if err := m.sendPixels(); err != nil {
		m.gfx.Overlay.Clear()
		m.canvas = render.CanvasQuadrant
		m.recordStatus = "Pixels unavailable: " + err.Error()
		m.resize(m.width, m.height)
		m.draw()
		return
	}
	m.cachedView = m.blank
}

// sendPixels encodes the buffer for the session's protocol. Kitty keeps an image
// once placed, so it is sent once per change. Sixel and iTerm2 images live in cells
// any repaint can overwrite, so the overlay re-sends them after every flush.
func (m *ViewerModel) sendPixels() error {
	img := m.pbuf.ToImage(color.RGBA{A: 0xFF})
	cols, rows := m.canvasCells()
	ov := m.gfx.Overlay
	switch m.gfx.Protocol {
	case graphics.Kitty:
		ov.Once(pixelRow, pixelCol, graphics.EncodeKitty(graphics.EncodePNG(img), kittyID, cols, rows))
	case graphics.ITerm2:
		ov.Set(pixelRow, pixelCol, graphics.EncodeITerm2(graphics.EncodePNG(img), cols, rows))
	case graphics.Sixel:
		seq, err := graphics.EncodeSixel(img)
		if err != nil {
			return err
		}
		ov.Set(pixelRow, pixelCol, seq)
	}
	return nil
}

func (m *ViewerModel) stateLine() string {
	// Whole degrees while the user drives (steps are 5 degrees); tenths while rocking,
	// where a tick moves under a degree and a stale line would skip the flush.
	degFmt := "%.0f"
	if m.rocking {
		degFmt = "%.1f"
	}
	deg := func(r float64) string { return fmt.Sprintf(degFmt, r*180/math.Pi) }
	return fmt.Sprintf("frame %d/%d  fps %d  zoom %.2fx  rot %s %s  pan %.0f %.0f  H %s  cartoon %s  rock %s  canvas %s  mode %s",
		m.frameIdx+1, len(m.mol.Frames), m.playFPS, m.zoom, deg(m.rotX), deg(m.rotY), m.panX, m.panY,
		onOff(m.showH), onOff(m.showCartoon), onOff(m.rocking), m.canvas.Name(), strings.ToLower(modeName(m.mode)))
}

func onOff(b bool) string {
	if b {
		return "on"
	}
	return "off"
}

// startRecording kicks off an async export of the full trajectory to a GIF using
// the current camera. The work runs in the returned tea.Cmd so the UI stays live.
func (m *ViewerModel) startRecording() tea.Cmd {
	if m.recording || m.mol == nil || len(m.mol.Frames) == 0 || m.pbuf == nil {
		return nil
	}
	m.recording = true
	m.recordStatus = fmt.Sprintf("Recording %d frames...", len(m.mol.Frames))

	params := render.RecordParams{
		Mol:         m.mol,
		Mode:        m.mode,
		ShowCartoon: m.showCartoon,
		Cfg:         m.sceneConfig(),
		TermW:       m.pbuf.TermW,
		TermH:       m.pbuf.TermH,
		PerCellX:    m.pbuf.PerCellX,
		PerCellY:    m.pbuf.PerCellY,
		FPS:         m.playFPS,
		Background:  color.RGBA{R: 0, G: 0, B: 0, A: 0xFF},
	}
	path := m.recordingPath()
	return func() tea.Msg {
		err := render.RecordTrajectoryGIF(path, params)
		return RecordDoneMsg{Path: path, Err: err}
	}
}

func (m *ViewerModel) recordingPath() string {
	base := m.srcPath
	if base == "" {
		base = "molecule.pdb"
	}
	dir := filepath.Dir(base)
	name := strings.TrimSuffix(filepath.Base(base), filepath.Ext(base))
	stamp := time.Now().Format("20060102_150405")
	return filepath.Join(dir, fmt.Sprintf("%s_recording_%s.gif", name, stamp))
}

func (m *ViewerModel) advanceFrame(delta int) {
	if m.mol == nil || len(m.mol.Frames) == 0 {
		return
	}
	n := len(m.mol.Frames)
	m.frameIdx = (m.frameIdx + delta + n) % n
	m.dirty = true
}

func (m *ViewerModel) sidebarView() string {
	frameCount := len(m.mol.Frames)

	title := m.mol.Title
	if len(title) > 24 {
		title = title[:24]
	}

	ssCount := len(m.mol.SSSegments)

	lines := []string{
		"MOLECULE",
		"",
		"File: " + title,
		fmt.Sprintf("Atoms: %d", len(m.mol.Frames[m.frameIdx].Atoms)),
		fmt.Sprintf("Mode: %s", modeName(m.mode)),
		fmt.Sprintf("Canvas: %s %dx%d", m.canvas.Name(), m.pbuf.PerCellX, m.pbuf.PerCellY),
		fmt.Sprintf("Cartoon: %s", onOff(m.showCartoon)),
		fmt.Sprintf("SS segs: %d", ssCount),
		fmt.Sprintf("Frame: %d/%d", m.frameIdx+1, frameCount),
		fmt.Sprintf("Zoom: %.2fx", m.zoom),
		fmt.Sprintf("FPS: %d", m.playFPS),
		fmt.Sprintf("Playing: %t", m.playing),
	}
	return strings.Join(lines, "\n")
}

func modeName(mode render.RenderMode) string {
	switch mode {
	case render.ModeBallStick:
		return "BallStick"
	case render.ModeWireframe:
		return "Wireframe"
	case render.ModeCartoon:
		return "Cartoon"
	default:
		return "SpaceFill"
	}
}

func autoMode(mol *pdb.Molecule) render.RenderMode {
	if mol == nil || len(mol.Frames) == 0 {
		return render.ModeSpaceFill
	}
	n := len(mol.Frames[0].Atoms)
	if n < 5000 {
		return render.ModeSpaceFill
	}
	if n <= 20000 {
		return render.ModeBallStick
	}
	return render.ModeWireframe
}

func tickCmd(fps int) tea.Cmd {
	if fps < 1 {
		fps = 1
	}
	d := time.Second / time.Duration(fps)
	return tea.Tick(d, func(time.Time) tea.Msg { return TickMsg{} })
}

func rockTickCmd() tea.Cmd {
	return tea.Tick(time.Second/rockFPS, func(time.Time) tea.Msg { return RockTickMsg{} })
}

func joinHorizontalTop(left, right string) string {
	lLines := strings.Split(left, "\n")
	rLines := strings.Split(right, "\n")
	rows := max(len(lLines), len(rLines))
	var out strings.Builder
	for i := 0; i < rows; i++ {
		if i < len(lLines) {
			out.WriteString(lLines[i])
		}
		if i < len(rLines) {
			out.WriteString(rLines[i])
		}
		if i < rows-1 {
			out.WriteByte('\n')
		}
	}
	return out.String()
}
