package model

import (
	"encoding/json"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/CyrusCorrell0/amide/tui/pdb"
	"github.com/CyrusCorrell0/amide/tui/render"
	"github.com/CyrusCorrell0/amide/tui/ui"
	"github.com/charmbracelet/bubbles/list"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// Row is one `amide info` row plus index bookkeeping; the json tags are the Python keys.
type Row struct {
	Path      string     `json:"path"`
	ID        *string    `json:"id"`
	Title     *string    `json:"title"`
	Frames    int        `json:"frames"`
	Atoms     int        `json:"atoms"`
	Hydrogens int        `json:"hydrogens"`
	Chains    []string   `json:"chains"`
	Residues  int        `json:"residues"`
	Extent    [3]float64 `json:"extent"`
	Size      int64      `json:"size"`
	Modified  float64    `json:"modified"`
	Indexed   float64    `json:"indexed"`
}

// RowsFile is the document `amide search` writes: the rows, already filtered and sorted, and how.
type RowsFile struct {
	Query   string `json:"query"`
	Sort    string `json:"sort"`
	Reverse bool   `json:"reverse"`
	Rows    []Row  `json:"rows"`
}

// LoadRows reads the rows file `amide search` writes; a missing sort means "path".
func LoadRows(path string) (RowsFile, error) {
	var doc RowsFile
	data, err := os.ReadFile(path)
	if err != nil {
		return doc, err
	}
	if err := json.Unmarshal(data, &doc); err != nil {
		return doc, fmt.Errorf("%s: %w", path, err)
	}
	if doc.Sort == "" {
		doc.Sort = "path"
	}
	return doc, nil
}

// sortKeys matches the Python index SORT_KEYS, in the same order.
var sortKeys = []string{"path", "id", "frames", "atoms", "hydrogens", "residues", "size", "modified"}

const (
	previewDelay = 80 * time.Millisecond
	spinEvery    = time.Second / 12
	spinStep     = 0.03 // radians per spin tick
	noticeFor    = 2 * time.Second
	cacheSize    = 16
	minListW     = 36
	minPreviewW  = 60 // below this total width the preview pane is dropped
)

type previewTickMsg struct{ path string }

type previewLoadedMsg struct {
	path string
	mol  *pdb.Molecule
	err  error
}

type spinTickMsg struct{}

// copiedMsg reports whether the highlighted path reached the clipboard.
type copiedMsg struct {
	path string
	err  error
}

type noticeClearMsg struct{}

// rowFormat lays out one row and the column header above it: id, frames, atoms, chains.
const rowFormat = "%-6s %8s %10s  %s"

var columnHeader = fmt.Sprintf(rowFormat, "PDB", "FRAMES", "ATOMS", "CHAINS")

type searchItem struct{ row Row }

func (i searchItem) FilterValue() string {
	parts := make([]string, 0, 3)
	if i.row.ID != nil {
		parts = append(parts, *i.row.ID)
	}
	if i.row.Title != nil {
		parts = append(parts, *i.row.Title)
	}
	return strings.Join(append(parts, filepath.Base(i.row.Path)), " ")
}

// rowDelegate draws each row on one line: id, frames, atoms, chains.
type rowDelegate struct{}

func (rowDelegate) Height() int                         { return 1 }
func (rowDelegate) Spacing() int                        { return 0 }
func (rowDelegate) Update(tea.Msg, *list.Model) tea.Cmd { return nil }
func (rowDelegate) Render(w io.Writer, m list.Model, index int, item list.Item) {
	r := item.(searchItem).row
	line := fmt.Sprintf(rowFormat, deref(r.ID, "-"), fmt.Sprint(r.Frames), fmt.Sprint(r.Atoms), strings.Join(r.Chains, ""))
	style, cursor := lipgloss.NewStyle(), "  "
	if index == m.Index() {
		style, cursor = lipgloss.NewStyle().Foreground(lipgloss.Color(ui.ColorAccent)).Bold(true), "> "
	}
	fmt.Fprint(w, style.MaxWidth(max(1, m.Width())).Render(cursor+line))
}

// preview is the molecule shown in the right pane and how far it has spun.
type preview struct {
	path  string
	mol   *pdb.Molecule
	err   error
	rotY  float64
	pbuf  *render.PixelBuffer
	drawn string
	dirty bool
}

// SearchModel is the search screen: indexed rows on the left, a spinning preview on the right.
type SearchModel struct {
	list          list.Model
	rows          []Row
	sortKey       int // index into sortKeys
	reverse       bool
	sorted        bool // false until s or r: the rows keep the order Python sorted them in
	preview       *preview
	cache         map[string]*pdb.Molecule
	cacheOrder    []string
	spinning      bool
	keep          string // path to re-select once an async filter pass lands
	width, height int
	query         string
	title         string
	notice        string // replaces the status line briefly, after a copy
}

// NewSearchModel builds the screen over doc's rows, in the order given, with its query in the title.
func NewSearchModel(doc RowsFile, width, height int) *SearchModel {
	query := doc.Query
	l := list.New(nil, rowDelegate{}, 1, 1)
	l.SetShowTitle(false) // the screen draws its own title and column header
	l.SetShowStatusBar(false)
	l.SetShowHelp(false)
	l.DisableQuitKeybindings()
	l.FilterInput.Prompt = "filter: "
	m := &SearchModel{
		list: l, rows: doc.Rows, reverse: doc.Reverse, query: query,
		title: strings.TrimSpace("amide search  " + query), cache: map[string]*pdb.Molecule{},
	}
	for i, k := range sortKeys {
		if k == doc.Sort {
			m.sortKey = i
		}
	}
	m.list.SetItems(m.items())
	m.SetSize(width, height)
	return m
}

// Init schedules the first preview so the pane is not blank on open.
func (m *SearchModel) Init() tea.Cmd { return m.schedulePreview() }

// SetSize lays the screen out for a terminal of width by height cells.
func (m *SearchModel) SetSize(width, height int) {
	m.width, m.height = max(1, width), max(1, height)
	m.list.SetSize(m.listWidth(), max(1, m.height-3)) // title, header, status
	if m.preview != nil {
		m.preview.dirty = true
	}
}

// Filtering reports whether the user is typing a filter, so the app leaves q, esc and enter alone.
func (m *SearchModel) Filtering() bool { return m.list.SettingFilter() }

// FilterState is the list's filter state, so esc can clear an applied filter before it quits.
func (m *SearchModel) FilterState() list.FilterState { return m.list.FilterState() }

// SelectedPath is the path of the highlighted row, or "" when nothing is listed.
func (m *SearchModel) SelectedPath() string {
	item, ok := m.list.SelectedItem().(searchItem)
	if !ok {
		return ""
	}
	return item.row.Path
}

// Update handles keys, debounced preview loading and the spin tick.
func (m *SearchModel) Update(msg tea.Msg) tea.Cmd {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.SetSize(msg.Width, msg.Height)
		return m.startSpin()
	case previewTickMsg:
		if msg.path != m.SelectedPath() {
			return nil
		}
		if mol, ok := m.cache[msg.path]; ok {
			return m.show(msg.path, mol, nil)
		}
		path := msg.path
		return func() tea.Msg {
			mol, err := pdb.ParseFirstFrame(path)
			return previewLoadedMsg{path: path, mol: mol, err: err}
		}
	case previewLoadedMsg:
		if msg.err == nil {
			m.remember(msg.path, msg.mol)
		}
		if msg.path != m.SelectedPath() {
			return nil
		}
		return m.show(msg.path, msg.mol, msg.err)
	case spinTickMsg:
		if m.preview == nil || m.preview.mol == nil || !m.hasPreviewPane() {
			m.spinning = false
			return nil
		}
		m.preview.rotY += spinStep
		m.preview.dirty = true
		return tea.Tick(spinEvery, func(time.Time) tea.Msg { return spinTickMsg{} })
	case copiedMsg:
		m.notice = "copied " + msg.path
		if msg.err != nil {
			m.notice = "could not copy: " + msg.err.Error()
		}
		return tea.Tick(noticeFor, func(time.Time) tea.Msg { return noticeClearMsg{} })
	case noticeClearMsg:
		m.notice = ""
		return nil
	case tea.KeyMsg:
		if !m.list.SettingFilter() {
			switch msg.String() {
			case "c":
				path := m.SelectedPath()
				if path == "" {
					return nil
				}
				return func() tea.Msg { return copiedMsg{path: path, err: copyText(path)} }
			case "s":
				m.sortKey = (m.sortKey + 1) % len(sortKeys)
				m.sorted = true
				return m.resort()
			case "r":
				m.reverse = !m.reverse
				m.sorted = true
				return m.resort()
			}
		}
	}

	before := m.SelectedPath()
	var cmd tea.Cmd
	m.list, cmd = m.list.Update(msg)
	if _, ok := msg.(list.FilterMatchesMsg); ok && m.keep != "" {
		m.selectPath(m.keep)
		m.keep = ""
	}
	if m.SelectedPath() != before {
		return tea.Batch(cmd, m.schedulePreview())
	}
	return cmd
}

// View draws the list, the preview pane when there is room, and the status line.
func (m *SearchModel) View() string {
	left := m.headerView() + "\n" + ui.MutedStyle.Render("  "+columnHeader) + "\n"
	if len(m.rows) == 0 {
		left += ui.MutedStyle.Render("  nothing indexed matches")
	} else {
		left += m.list.View()
	}
	left = lipgloss.NewStyle().Width(m.listWidth()).Height(m.height - 1).Render(left)
	top := left
	if m.hasPreviewPane() {
		top = lipgloss.JoinHorizontal(lipgloss.Top, left, m.paneView())
	}
	status := m.notice
	if status == "" {
		status = fmt.Sprintf("type to filter  enter open  c copy path  s sort: %s  r reverse  esc quit", sortKeys[m.sortKey])
	}
	return lipgloss.NewStyle().MaxWidth(m.width).MaxHeight(m.height).Render(top + "\n" + ui.MutedStyle.Render(status))
}

// headerView is the top line: the filter being typed, or the title and any applied filter.
func (m *SearchModel) headerView() string {
	if m.list.SettingFilter() {
		return m.list.FilterInput.View()
	}
	title := ui.HeaderStyle.Render(m.title)
	if m.list.FilterState() == list.FilterApplied {
		title += ui.MutedStyle.Render("  filter: " + m.list.FilterValue())
	}
	return title
}

func (m *SearchModel) paneView() string {
	w := m.width - m.listWidth()
	item, ok := m.list.SelectedItem().(searchItem)
	if !ok {
		return ""
	}
	clip := lipgloss.NewStyle().MaxWidth(w)
	p := m.preview
	if p != nil && p.err != nil && p.path == item.row.Path {
		return clip.Render(fmt.Sprintf("could not read %s: %v", filepath.Base(p.path), p.err))
	}
	innerW, innerH := max(1, w-4), max(1, m.height-1-4-2) // status, facts, border
	if p != nil && p.mol != nil {
		if p.pbuf == nil || p.pbuf.TermW != innerW || p.pbuf.TermH != innerH {
			p.pbuf = render.NewPixelBuffer(innerW, innerH)
			p.dirty = true
		}
		if p.dirty {
			render.RenderFramePixel(p.pbuf, p.mol.Frames[0], autoModeFor(p.mol), m.scene(p, innerH+2))
			p.drawn, p.dirty = p.pbuf.Render(), false
		}
	}
	drawn := ""
	if p != nil && p.mol != nil {
		drawn = p.drawn
	}
	canvas := ui.CanvasStyle.Width(innerW + 2).Height(innerH).Render(drawn)
	r := item.row
	facts := []string{
		filepath.Base(r.Path),
		fmt.Sprintf("%s  %s  %s", plural(r.Frames, "frame"), plural(r.Atoms, "atom"), strings.Join(r.Chains, "")),
		fmt.Sprintf("%s  %.1f x %.1f x %.1f", plural(r.Residues, "residue"), r.Extent[0], r.Extent[1], r.Extent[2]),
		humanSize(r.Size) + "  " + r.Path,
	}
	for i, f := range facts {
		facts[i] = clip.Render(f)
	}
	return canvas + "\n" + ui.MutedStyle.Render(strings.Join(facts, "\n"))
}

// scene fills SceneConfig the way viewer.go View does, with zoom from viewer.go resetView.
func (m *SearchModel) scene(p *preview, canvasH int) render.SceneConfig {
	zoom := 12.0
	if p.mol.Extent != 0 {
		zoom = math.Max(2.0, float64(max(10, canvasH))/p.mol.Extent)
	}
	return render.SceneConfig{
		CenterX: p.mol.CenterX, CenterY: p.mol.CenterY, CenterZ: p.mol.CenterZ,
		RotX: 0.3, RotY: p.rotY, Zoom: zoom,
	}
}

// autoModeFor is the atom-count rule of autoMode in viewer.go, copied so the viewer stays untouched.
func autoModeFor(mol *pdb.Molecule) render.RenderMode {
	n := len(mol.Frames[0].Atoms)
	switch {
	case n < 5000:
		return render.ModeSpaceFill
	case n <= 20000:
		return render.ModeBallStick
	}
	return render.ModeWireframe
}

func (m *SearchModel) show(path string, mol *pdb.Molecule, err error) tea.Cmd {
	if err == nil && (mol == nil || len(mol.Frames) == 0) {
		mol, err = nil, fmt.Errorf("no atoms")
	}
	m.preview = &preview{path: path, mol: mol, err: err, rotY: 0.6, dirty: true}
	return m.startSpin()
}

func (m *SearchModel) startSpin() tea.Cmd {
	if m.spinning || m.preview == nil || m.preview.mol == nil || !m.hasPreviewPane() {
		return nil
	}
	m.spinning = true
	return tea.Tick(spinEvery, func(time.Time) tea.Msg { return spinTickMsg{} })
}

func (m *SearchModel) schedulePreview() tea.Cmd {
	path := m.SelectedPath()
	if path == "" {
		return nil
	}
	return tea.Tick(previewDelay, func(time.Time) tea.Msg { return previewTickMsg{path: path} })
}

func (m *SearchModel) remember(path string, mol *pdb.Molecule) {
	if _, ok := m.cache[path]; ok {
		return
	}
	if len(m.cacheOrder) >= cacheSize {
		delete(m.cache, m.cacheOrder[0])
		m.cacheOrder = m.cacheOrder[1:]
	}
	m.cache[path] = mol
	m.cacheOrder = append(m.cacheOrder, path)
}

func (m *SearchModel) resort() tea.Cmd {
	keep := m.SelectedPath()
	cmd := m.list.SetItems(m.items())
	m.selectPath(keep)
	if cmd != nil {
		m.keep = keep // a filter is applied; visible items refresh when its pass lands
	}
	return cmd
}

func (m *SearchModel) selectPath(path string) {
	for i, it := range m.list.VisibleItems() {
		if it.(searchItem).row.Path == path {
			m.list.Select(i)
			return
		}
	}
}

// items returns the rows as list items in the current sort order. Missing ids sort last either way.
func (m *SearchModel) items() []list.Item {
	rows := append([]Row(nil), m.rows...)
	if m.sorted {
		key := sortKeys[m.sortKey]
		sort.SliceStable(rows, func(i, j int) bool {
			a, b := rows[i], rows[j]
			if key == "id" && (a.ID == nil) != (b.ID == nil) {
				return b.ID == nil
			}
			c := compareRows(a, b, key)
			if c == 0 {
				c = compareRows(a, b, "path")
			}
			if m.reverse {
				return c > 0
			}
			return c < 0
		})
	}
	items := make([]list.Item, len(rows))
	for i, r := range rows {
		items[i] = searchItem{r}
	}
	return items
}

// compareRows orders two rows by key; text keys compare case-insensitively.
func compareRows(a, b Row, key string) int {
	var x, y float64
	switch key {
	case "path":
		return strings.Compare(strings.ToLower(a.Path), strings.ToLower(b.Path))
	case "id":
		return strings.Compare(strings.ToLower(deref(a.ID, "")), strings.ToLower(deref(b.ID, "")))
	case "frames":
		x, y = float64(a.Frames), float64(b.Frames)
	case "atoms":
		x, y = float64(a.Atoms), float64(b.Atoms)
	case "hydrogens":
		x, y = float64(a.Hydrogens), float64(b.Hydrogens)
	case "residues":
		x, y = float64(a.Residues), float64(b.Residues)
	case "size":
		x, y = float64(a.Size), float64(b.Size)
	case "modified":
		x, y = a.Modified, b.Modified
	}
	switch {
	case x < y:
		return -1
	case x > y:
		return 1
	}
	return 0
}

func (m *SearchModel) listWidth() int {
	if !m.hasPreviewPane() {
		return m.width
	}
	return max(minListW, m.width*40/100)
}

func (m *SearchModel) hasPreviewPane() bool { return m.width >= minPreviewW && len(m.rows) > 0 }

func deref(s *string, fallback string) string {
	if s == nil || *s == "" {
		return fallback
	}
	return *s
}

func plural(n int, noun string) string {
	if n == 1 {
		return "1 " + noun
	}
	return fmt.Sprintf("%d %ss", n, noun)
}
