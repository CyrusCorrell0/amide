package model

import (
	"testing"

	"github.com/CyrusCorrell0/amide/tui/graphics"
	tea "github.com/charmbracelet/bubbletea"
)

func searchApp() *AppModel {
	return NewSearchApp(RowsFile{Query: "q", Sort: "path", Rows: []Row{{Path: "a.pdb", Frames: 1, Atoms: 3}}}, graphics.NewSession(graphics.None, 0, 0))
}

func TestNewSearchAppStartsOnSearch(t *testing.T) {
	if got := searchApp().screen; got != ScreenSearch {
		t.Fatalf("screen = %v, want ScreenSearch", got)
	}
}

func TestBackToPickerReturnsToSearch(t *testing.T) {
	m := searchApp()
	m.screen = ScreenViewer
	m.Update(BackToPickerMsg{})
	if m.screen != ScreenSearch {
		t.Fatalf("screen = %v after BackToPickerMsg, want ScreenSearch", m.screen)
	}
}

func TestEscOnUnfilteredSearchQuits(t *testing.T) {
	_, cmd := searchApp().Update(tea.KeyMsg{Type: tea.KeyEsc})
	if cmd == nil {
		t.Fatal("esc returned no command, want tea.Quit")
	}
	if _, ok := cmd().(tea.QuitMsg); !ok {
		t.Fatalf("esc command yields %T, want tea.QuitMsg", cmd())
	}
}

func TestNewAppModelStartsOnPicker(t *testing.T) {
	if got := NewAppModel(t.TempDir(), "", graphics.NewSession(graphics.None, 0, 0)).screen; got != ScreenFilePicker {
		t.Fatalf("screen = %v, want ScreenFilePicker", got)
	}
}
