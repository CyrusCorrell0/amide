package ui

import "github.com/charmbracelet/lipgloss"

const (
	ColorBackground = "#000000"
	ColorBorder     = "#30363D"
	ColorAccent     = "#58A6FF"
	ColorMuted      = "#8B949E"
	ColorHighlight  = "#F0F6FC"
)

var (
	AppStyle = lipgloss.NewStyle().
			Background(lipgloss.Color(ColorBackground)).
			Foreground(lipgloss.Color(ColorHighlight))

	HeaderStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color(ColorAccent)).
			Bold(true).
			Padding(0, 1)

	MutedStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color(ColorMuted))

	CanvasStyle = lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(lipgloss.Color(ColorAccent)).
			Background(lipgloss.Color(ColorBackground)).
			Padding(0, 1)

	SidebarStyle = lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(lipgloss.Color(ColorBorder)).
			Background(lipgloss.Color(ColorBackground)).
			Padding(0, 1)

	StatusBarStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color(ColorMuted)).
			Background(lipgloss.Color(ColorBackground)).
			Padding(0, 1)

	ErrorStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("#FF6B6B")).
			Bold(true)
)
