package model

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/CyrusCorrell0/amide/tui/ui"
	"github.com/charmbracelet/bubbles/list"
	tea "github.com/charmbracelet/bubbletea"
)

type pdbFileItem struct {
	name string
	path string
	size int64
}

func (i pdbFileItem) Title() string       { return i.name }
func (i pdbFileItem) Description() string { return humanSize(i.size) }
func (i pdbFileItem) FilterValue() string { return i.name }

type FilePickerModel struct {
	list  list.Model
	root  string
	empty bool
}

// NewFilePickerModel lists the .pdb files directly under root.
func NewFilePickerModel(root string, width, height int) *FilePickerModel {
	l := list.New(nil, list.NewDefaultDelegate(), max(1, width-2), max(1, height-5))
	l.Title = "PDB Files"
	l.SetShowStatusBar(false)
	l.SetFilteringEnabled(true)
	l.SetShowHelp(false)
	l.Styles.Title = ui.HeaderStyle

	m := &FilePickerModel{
		list: l,
		root: root,
	}
	_ = m.Scan()
	m.SetSize(width, height)
	return m
}

func (m *FilePickerModel) Scan() error {
	entries, err := os.ReadDir(m.root)
	if err != nil {
		return err
	}

	items := make([]list.Item, 0)
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		name := entry.Name()
		if !strings.EqualFold(filepath.Ext(name), ".pdb") {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			continue
		}
		items = append(items, pdbFileItem{
			name: name,
			path: filepath.Join(m.root, name),
			size: info.Size(),
		})
	}

	sort.Slice(items, func(i, j int) bool {
		return items[i].FilterValue() < items[j].FilterValue()
	})

	m.empty = len(items) == 0
	m.list.SetItems(items)
	if len(items) > 0 {
		m.list.Select(0)
	}
	return nil
}

func (m *FilePickerModel) SetSize(width, height int) {
	m.list.SetSize(max(1, width-2), max(1, height-5))
}

func (m *FilePickerModel) Update(msg tea.Msg) tea.Cmd {
	var cmd tea.Cmd
	m.list, cmd = m.list.Update(msg)
	return cmd
}

func (m *FilePickerModel) SelectedPath() string {
	item, ok := m.list.SelectedItem().(pdbFileItem)
	if !ok {
		return ""
	}
	return item.path
}

func (m *FilePickerModel) View(width, height int) string {
	header := ui.HeaderStyle.Render(fmt.Sprintf("amide  [%s]", m.root))
	help := ui.MutedStyle.Render("up/down or j/k navigate   enter select   q quit")
	if m.empty {
		empty := ui.MutedStyle.Render(fmt.Sprintf("No .pdb files found in %s.", m.root))
		body := strings.Join([]string{header, "", empty, "", help}, "\n")
		return ui.AppStyle.Width(max(1, width)).Height(max(1, height)).Render(body)
	}

	body := strings.Join([]string{
		header,
		m.list.View(),
		help,
	}, "\n")
	return ui.AppStyle.Width(max(1, width)).Height(max(1, height)).Render(body)
}

func humanSize(size int64) string {
	const unit = 1024
	if size < unit {
		return fmt.Sprintf("%d B", size)
	}
	div, exp := int64(unit), 0
	for n := size / unit; n >= unit; n /= unit {
		div *= unit
		exp++
	}
	return fmt.Sprintf("%.1f %cB", float64(size)/float64(div), "KMGTPE"[exp])
}
