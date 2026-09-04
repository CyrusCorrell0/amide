package model

import (
	"fmt"
	"image/color"
	"math"
	"path/filepath"
	"strings"
	"time"

	"github.com/CyrusCorrell0/amide/tui/pdb"
	"github.com/CyrusCorrell0/amide/tui/render"
	"github.com/CyrusCorrell0/amide/tui/ui"
	tea "github.com/charmbracelet/bubbletea"
)

type BackToPickerMsg struct{}
type TickMsg struct{}

// RecordDoneMsg reports the result of an async GIF export.
type RecordDoneMsg struct {
	Path string
	Err  error
}

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
	showH, showSidebar bool
	showCartoon        bool
	dirty              bool
	cachedView         string
	keys               ui.ViewerKeyMap
	srcPath            string
	recording          bool
	recordStatus       string
}

func NewViewerModel(width, height int, mol *pdb.Molecule, srcPath string) *ViewerModel {
	m := &ViewerModel{
		width:       width,
		height:      height,
		mol:         mol,
		srcPath:     srcPath,
		playFPS:     10,
		showH:       false,
		showSidebar: true,
		mode:        render.ModeBallStick,
		keys:        ui.DefaultViewerKeys(),
		dirty:       true,
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

	if m.pbuf == nil {
		m.pbuf = render.NewPixelBuffer(max(1, m.canvasW-4), max(1, m.canvasH-2))
	} else {
		m.pbuf.Resize(max(1, m.canvasW-4), max(1, m.canvasH-2))
	}
	m.dirty = true
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

func (m *ViewerModel) Update(msg tea.Msg) tea.Cmd {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.resize(msg.Width, msg.Height)
		return nil
	case TickMsg:
		if !m.playing || m.mol == nil || len(m.mol.Frames) < 2 {
			return nil
		}
		m.frameIdx = (m.frameIdx + 1) % len(m.mol.Frames)
		m.dirty = true
		return tickCmd(m.playFPS)
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
			m.resize(m.width, m.height)
		case msg.String() == "0":
			m.resetView()
			m.dirty = true
		case msg.String() == "+" || msg.String() == "=":
			m.zoom *= 1.15
			m.dirty = true
		case msg.String() == "-" || msg.String() == "_":
			m.zoom = math.Max(0.5, m.zoom/1.15)
			m.dirty = true
		case msg.String() == "left" || msg.String() == "h" || msg.String() == "a":
			m.rotY -= 5.0 * math.Pi / 180.0
			m.dirty = true
		case msg.String() == "right" || msg.String() == "l" || msg.String() == "d":
			m.rotY += 5.0 * math.Pi / 180.0
			m.dirty = true
		case msg.String() == "up" || msg.String() == "k" || msg.String() == "w":
			m.rotX -= 5.0 * math.Pi / 180.0
			m.dirty = true
		case msg.String() == "down" || msg.String() == "j" || msg.String() == "s":
			m.rotX += 5.0 * math.Pi / 180.0
			m.dirty = true
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
		cfg := render.SceneConfig{
			CenterX:      m.mol.CenterX,
			CenterY:      m.mol.CenterY,
			CenterZ:      m.mol.CenterZ,
			RotX:         m.rotX,
			RotY:         m.rotY,
			Zoom:         m.zoom,
			PanX:         m.panX,
			PanY:         m.panY,
			ShowHydrogen: m.showH,
		}
		frame := m.mol.Frames[m.frameIdx]
		if m.showCartoon || m.mode == render.ModeCartoon {
			render.RenderCartoonPixel(m.pbuf, frame, m.mol.SSSegments, cfg)
		} else {
			render.RenderFramePixel(m.pbuf, frame, m.mode, cfg)
		}
		m.cachedView = m.pbuf.Render()
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

	status := ui.StatusBarStyle.Width(max(1, m.width-2)).Render(
		"[hjkl/arrows] rotate  [HJKL] pan  [+/-] zoom  [space] play  [.,] frame  [m] mode  [q] back",
	) + "\n" +
		ui.StatusBarStyle.Width(max(1, m.width-2)).Render(
			fmt.Sprintf("[c] cartoon:%t  [e] H:%t  [tab] sidebar  [[/]] fps:%d  [R] record  [0] reset",
				m.showCartoon, m.showH, m.playFPS),
		)

	if m.recordStatus != "" {
		status += "\n" + ui.StatusBarStyle.Width(max(1, m.width-2)).Render(m.recordStatus)
	}

	return ui.AppStyle.Width(max(1, m.width)).Height(max(1, m.height)).Render(top + "\n" + status)
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
		Cfg: render.SceneConfig{
			CenterX:      m.mol.CenterX,
			CenterY:      m.mol.CenterY,
			CenterZ:      m.mol.CenterZ,
			RotX:         m.rotX,
			RotY:         m.rotY,
			Zoom:         m.zoom,
			PanX:         m.panX,
			PanY:         m.panY,
			ShowHydrogen: m.showH,
		},
		TermW:      m.pbuf.TermW,
		TermH:      m.pbuf.TermH,
		FPS:        m.playFPS,
		Background: color.RGBA{R: 0, G: 0, B: 0, A: 0xFF},
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
	modeName := "SpaceFill"
	switch m.mode {
	case render.ModeBallStick:
		modeName = "BallStick"
	case render.ModeWireframe:
		modeName = "Wireframe"
	case render.ModeCartoon:
		modeName = "Cartoon (SS)"
	}

	title := m.mol.Title
	if len(title) > 24 {
		title = title[:24]
	}

	cartoonName := "off"
	if m.showCartoon {
		cartoonName = "on"
	}
	ssCount := len(m.mol.SSSegments)

	lines := []string{
		"MOLECULE",
		"",
		"File: " + title,
		fmt.Sprintf("Atoms: %d", len(m.mol.Frames[m.frameIdx].Atoms)),
		fmt.Sprintf("Mode: %s", modeName),
		fmt.Sprintf("Cartoon: %s", cartoonName),
		fmt.Sprintf("SS segs: %d", ssCount),
		fmt.Sprintf("Frame: %d/%d", m.frameIdx+1, frameCount),
		fmt.Sprintf("Zoom: %.2fx", m.zoom),
		fmt.Sprintf("FPS: %d", m.playFPS),
		fmt.Sprintf("Playing: %t", m.playing),
	}
	return strings.Join(lines, "\n")
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
