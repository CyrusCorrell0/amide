package ui

import "github.com/charmbracelet/bubbles/key"

type ViewerKeyMap struct {
	RotateLeft    key.Binding
	RotateRight   key.Binding
	RotateUp      key.Binding
	RotateDown    key.Binding
	PanLeft       key.Binding
	PanRight      key.Binding
	PanUp         key.Binding
	PanDown       key.Binding
	ZoomIn        key.Binding
	ZoomOut       key.Binding
	PlayPause     key.Binding
	PrevFrame     key.Binding
	NextFrame     key.Binding
	Slower        key.Binding
	Faster        key.Binding
	CycleMode     key.Binding
	ToggleH       key.Binding
	ToggleSide    key.Binding
	TogglePixel   key.Binding
	ToggleCartoon key.Binding
	Reset         key.Binding
	Back          key.Binding
	Quit          key.Binding
}

func DefaultViewerKeys() ViewerKeyMap {
	return ViewerKeyMap{
		RotateLeft:    key.NewBinding(key.WithKeys("left", "h", "a")),
		RotateRight:   key.NewBinding(key.WithKeys("right", "l", "d")),
		RotateUp:      key.NewBinding(key.WithKeys("up", "k", "w")),
		RotateDown:    key.NewBinding(key.WithKeys("down", "j", "s")),
		PanLeft:       key.NewBinding(key.WithKeys("H")),
		PanRight:      key.NewBinding(key.WithKeys("L")),
		PanUp:         key.NewBinding(key.WithKeys("K")),
		PanDown:       key.NewBinding(key.WithKeys("J")),
		ZoomIn:        key.NewBinding(key.WithKeys("+", "=")),
		ZoomOut:       key.NewBinding(key.WithKeys("-", "_")),
		PlayPause:     key.NewBinding(key.WithKeys(" ")),
		PrevFrame:     key.NewBinding(key.WithKeys(",")),
		NextFrame:     key.NewBinding(key.WithKeys(".")),
		Slower:        key.NewBinding(key.WithKeys("[")),
		Faster:        key.NewBinding(key.WithKeys("]")),
		CycleMode:     key.NewBinding(key.WithKeys("m")),
		ToggleH:       key.NewBinding(key.WithKeys("e")),
		ToggleSide:    key.NewBinding(key.WithKeys("tab")),
		TogglePixel:   key.NewBinding(key.WithKeys("p")),
		ToggleCartoon: key.NewBinding(key.WithKeys("c")),
		Reset:         key.NewBinding(key.WithKeys("0")),
		Back:          key.NewBinding(key.WithKeys("q", "esc")),
		Quit:          key.NewBinding(key.WithKeys("ctrl+c")),
	}
}
