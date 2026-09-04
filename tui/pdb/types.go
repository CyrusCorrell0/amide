package pdb

type Atom struct {
	Serial  int
	Name    string
	ResName string
	ChainID byte
	ResSeq  int
	X, Y, Z float64
	Element string
	IsHet   bool
}

type Bond struct {
	A int
	B int
}

type Frame struct {
	Atoms []Atom
	Bonds []Bond
}

type Molecule struct {
	Title            string
	Frames           []Frame
	SSSegments       []SSSegment // from HELIX / SHEET records
	MinX, MaxX       float64
	MinY, MaxY       float64
	MinZ, MaxZ       float64
	CenterX, CenterY float64
	CenterZ          float64
	Extent           float64
}
