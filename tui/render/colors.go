package render

import (
	"strings"

	"github.com/charmbracelet/lipgloss"
)

var CPKColors = map[string]lipgloss.Color{
	"C":  lipgloss.Color("#909090"),
	"N":  lipgloss.Color("#4444FF"),
	"O":  lipgloss.Color("#FF4444"),
	"S":  lipgloss.Color("#FFFF44"),
	"H":  lipgloss.Color("#FFFFFF"),
	"P":  lipgloss.Color("#FFA500"),
	"CL": lipgloss.Color("#00DD00"),
	"F":  lipgloss.Color("#A0FFA0"),
}

var VdWRadii = map[string]float64{
	"H":  1.20,
	"C":  1.70,
	"N":  1.55,
	"O":  1.52,
	"F":  1.47,
	"P":  1.80,
	"S":  1.80,
	"CL": 1.75,
}

func ElementColor(el string) lipgloss.Color {
	e := strings.ToUpper(strings.TrimSpace(el))
	if c, ok := CPKColors[e]; ok {
		return c
	}
	return CPKColors["C"]
}

func VdWRadius(el string) float64 {
	e := strings.ToUpper(strings.TrimSpace(el))
	if r, ok := VdWRadii[e]; ok {
		return r
	}
	return VdWRadii["C"]
}
