package model

import (
	"strings"

	"github.com/CyrusCorrell0/amide/tui/graphics"
	"github.com/CyrusCorrell0/amide/tui/pdb"
	"github.com/CyrusCorrell0/amide/tui/ui"
	"github.com/charmbracelet/bubbles/list"
	tea "github.com/charmbracelet/bubbletea"
)

type Screen int

const (
	ScreenFilePicker Screen = iota
	ScreenViewer
	ScreenError
	ScreenSearch
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
	home          Screen // where the viewer and the error screen return to
	picker        *FilePickerModel
	search        *SearchModel
	viewer        *ViewerModel
	lastErr       string
	file          string
	gfx           *graphics.Session // handed to every viewer
}

// NewAppModel roots the file picker at dir. When file is not empty the viewer
// opens on it immediately and the picker stays one keypress away. gfx says how
// the viewer may draw images; nil means text only.
func NewAppModel(dir, file string, gfx *graphics.Session) *AppModel {
	return &AppModel{
		screen: ScreenFilePicker,
		home:   ScreenFilePicker,
		picker: NewFilePickerModel(dir, 120, 40),
		file:   file,
		gfx:    gfx,
	}
}

// NewSearchApp opens on the search screen over doc's rows; the viewer returns there.
func NewSearchApp(doc RowsFile, gfx *graphics.Session) *AppModel {
	return &AppModel{
		screen: ScreenSearch,
		home:   ScreenSearch,
		search: NewSearchModel(doc, 120, 40),
		gfx:    gfx,
	}
}

func (m *AppModel) Init() tea.Cmd {
	if m.home == ScreenSearch {
		return m.search.Init()
	}
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
		var cmds []tea.Cmd
		if m.search != nil {
			cmds = append(cmds, m.search.Update(msg)) // sizes the screen and restarts its spin
		}
		if m.viewer != nil {
			cmds = append(cmds, m.viewer.Update(msg))
		}
		return m, tea.Batch(cmds...)
	case MoleculeLoadedMsg:
		m.viewer = NewViewerModel(m.width, m.height, msg.Mol, msg.Path, m.gfx)
		m.screen = ScreenViewer
		return m, nil
	case MoleculeErrMsg:
		m.lastErr = msg.Err.Error()
		m.screen = ScreenError
		return m, nil
	case BackToPickerMsg:
		m.screen = m.home
		return m, nil
	case tea.KeyMsg:
		if msg.String() == "ctrl+c" {
			return m, tea.Quit
		}
	}

	// The search screen's preview and spin ticks keep arriving while the viewer
	// or the error screen is up; it must still see them or its spin never restarts.
	var bg tea.Cmd
	if m.search != nil && m.screen != ScreenSearch {
		switch msg.(type) {
		case tea.KeyMsg, tea.MouseMsg:
		default:
			bg = m.search.Update(msg)
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
	case ScreenSearch:
		if keyMsg, ok := msg.(tea.KeyMsg); ok && !m.search.Filtering() {
			switch keyMsg.String() {
			case "q":
				return m, tea.Quit
			case "esc":
				if m.search.FilterState() == list.Unfiltered {
					return m, tea.Quit
				}
			case "enter":
				if path := m.search.SelectedPath(); path != "" {
					return m, loadMoleculeCmd(path)
				}
				return m, nil
			}
		}
		return m, m.search.Update(msg)
	case ScreenViewer:
		if m.viewer != nil {
			return m, tea.Batch(bg, m.viewer.Update(msg))
		}
	case ScreenError:
		if keyMsg, ok := msg.(tea.KeyMsg); ok {
			if keyMsg.String() == "q" || keyMsg.String() == "esc" {
				m.screen = m.home
				return m, nil
			}
		}
	}
	return m, bg
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
	case ScreenSearch:
		return m.search.View()
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
