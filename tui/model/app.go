package model

import (
	"strings"

	"github.com/CyrusCorrell0/amide/tui/pdb"
	"github.com/CyrusCorrell0/amide/tui/ui"
	tea "github.com/charmbracelet/bubbletea"
)

type Screen int

const (
	ScreenFilePicker Screen = iota
	ScreenViewer
	ScreenError
)

type MoleculeLoadedMsg struct {
	Path string
	Mol  *pdb.Molecule
}

type MoleculeErrMsg struct {
	Path string
	Err  error
}

type AppModel struct {
	width, height int
	screen        Screen
	picker        *FilePickerModel
	viewer        *ViewerModel
	lastErr       string
	file          string
}

// NewAppModel roots the file picker at dir. When file is not empty the viewer
// opens on it immediately and the picker stays one keypress away.
func NewAppModel(dir, file string) *AppModel {
	return &AppModel{
		screen: ScreenFilePicker,
		picker: NewFilePickerModel(dir, 120, 40),
		file:   file,
	}
}

func (m *AppModel) Init() tea.Cmd {
	if m.file == "" {
		return nil
	}
	return loadMoleculeCmd(m.file)
}

func (m *AppModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width, m.height = msg.Width, msg.Height
		if m.picker != nil {
			m.picker.SetSize(msg.Width, msg.Height)
		}
		if m.viewer != nil {
			return m, m.viewer.Update(msg)
		}
	case MoleculeLoadedMsg:
		m.viewer = NewViewerModel(m.width, m.height, msg.Mol, msg.Path)
		m.screen = ScreenViewer
		return m, nil
	case MoleculeErrMsg:
		m.lastErr = msg.Err.Error()
		m.screen = ScreenError
		return m, nil
	case BackToPickerMsg:
		m.screen = ScreenFilePicker
		return m, nil
	case tea.KeyMsg:
		if msg.String() == "ctrl+c" {
			return m, tea.Quit
		}
	}

	switch m.screen {
	case ScreenFilePicker:
		if keyMsg, ok := msg.(tea.KeyMsg); ok {
			switch keyMsg.String() {
			case "q", "esc":
				return m, tea.Quit
			case "enter":
				path := m.picker.SelectedPath()
				if path == "" {
					return m, nil
				}
				return m, loadMoleculeCmd(path)
			}
		}
		if m.picker != nil {
			return m, m.picker.Update(msg)
		}
	case ScreenViewer:
		if m.viewer != nil {
			return m, m.viewer.Update(msg)
		}
	case ScreenError:
		if keyMsg, ok := msg.(tea.KeyMsg); ok {
			if keyMsg.String() == "q" || keyMsg.String() == "esc" {
				m.screen = ScreenFilePicker
				return m, nil
			}
		}
	}
	return m, nil
}

func (m *AppModel) View() string {
	switch m.screen {
	case ScreenFilePicker:
		if m.picker == nil {
			return ui.ErrorStyle.Render("file picker unavailable")
		}
		return m.picker.View(max(1, m.width), max(1, m.height))
	case ScreenViewer:
		if m.viewer == nil {
			return ui.ErrorStyle.Render("viewer unavailable")
		}
		return m.viewer.View()
	case ScreenError:
		lines := []string{
			ui.ErrorStyle.Render("Failed to load molecule"),
			"",
			m.lastErr,
			"",
			ui.MutedStyle.Render("Press q or esc to return"),
		}
		return ui.AppStyle.Width(max(1, m.width)).Height(max(1, m.height)).Render(strings.Join(lines, "\n"))
	default:
		return ""
	}
}

func loadMoleculeCmd(path string) tea.Cmd {
	return func() tea.Msg {
		mol, err := pdb.ParseFile(path)
		if err != nil {
			return MoleculeErrMsg{Path: path, Err: err}
		}
		return MoleculeLoadedMsg{Path: path, Mol: mol}
	}
}
