package pdb

import (
	"bufio"
	"fmt"
	"math"
	"os"
	"sort"
	"strconv"
	"strings"
)

const (
	gridCellSize  = 2.0
	bondThreshold = 2.1
)

func ParseFile(path string) (*Molecule, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	var (
		title              string
		frames             []Frame
		ssSegments         []SSSegment
		currentAtoms       []Atom
		currentBonds       []Bond
		seenBondPairs      = map[string]struct{}{}
		seenAnyModelRecord bool
	)

	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := scanner.Text()
		if len(line) < 6 {
			continue
		}

		record := strings.TrimSpace(line[0:6])
		switch record {
		case "TITLE":
			if title == "" {
				title = strings.TrimSpace(safeSlice(line, 10, len(line)))
			}
		case "MODEL":
			seenAnyModelRecord = true
			if len(currentAtoms) > 0 {
				frames = append(frames, Frame{
					Atoms: append([]Atom(nil), currentAtoms...),
					Bonds: append([]Bond(nil), currentBonds...),
				})
				currentAtoms = nil
				currentBonds = nil
				seenBondPairs = map[string]struct{}{}
			}
		case "ENDMDL":
			if len(currentAtoms) > 0 {
				frames = append(frames, Frame{
					Atoms: append([]Atom(nil), currentAtoms...),
					Bonds: append([]Bond(nil), currentBonds...),
				})
			}
			currentAtoms = nil
			currentBonds = nil
			seenBondPairs = map[string]struct{}{}
		case "HELIX":
			seg := parseHelix(line)
			if seg.EndSeq >= seg.StartSeq {
				ssSegments = append(ssSegments, seg)
			}
		case "SHEET":
			seg := parseSheet(line)
			if seg.EndSeq >= seg.StartSeq {
				ssSegments = append(ssSegments, seg)
			}
		case "ATOM", "HETATM":
			atom, ok := parseAtom(line, record == "HETATM", len(currentAtoms)+1)
			if !ok {
				continue
			}
			currentAtoms = append(currentAtoms, atom)
		case "CONECT":
			bonds := parseConect(line)
			for _, b := range bonds {
				k := bondKey(b.A, b.B)
				if _, exists := seenBondPairs[k]; exists {
					continue
				}
				seenBondPairs[k] = struct{}{}
				currentBonds = append(currentBonds, b)
			}
		}
	}

	if err := scanner.Err(); err != nil {
		return nil, err
	}

	if len(currentAtoms) > 0 {
		frames = append(frames, Frame{
			Atoms: append([]Atom(nil), currentAtoms...),
			Bonds: append([]Bond(nil), currentBonds...),
		})
	}

	if len(frames) == 0 {
		return nil, fmt.Errorf("no atoms found in %s", path)
	}

	for i := range frames {
		if len(frames[i].Atoms) == 0 {
			continue
		}
		if len(frames[i].Bonds) == 0 {
			frames[i].Bonds = inferBonds(frames[i].Atoms)
		}
	}

	if seenAnyModelRecord {
		nonEmpty := make([]Frame, 0, len(frames))
		for _, fr := range frames {
			if len(fr.Atoms) == 0 {
				continue
			}
			nonEmpty = append(nonEmpty, fr)
		}
		frames = nonEmpty
	}

	if len(frames) == 0 {
		return nil, fmt.Errorf("no non-empty frames in %s", path)
	}

	mol := &Molecule{
		Title:      title,
		Frames:     frames,
		SSSegments: ssSegments,
	}
	if mol.Title == "" {
		mol.Title = "Untitled Molecule"
	}

	computeBounds(mol)
	return mol, nil
}

func parseAtom(line string, isHet bool, fallbackSerial int) (Atom, bool) {
	if len(line) < 54 {
		return Atom{}, false
	}

	var atom Atom
	atom.IsHet = isHet
	atom.Name = strings.TrimSpace(safeSlice(line, 12, 16))
	atom.ResName = strings.TrimSpace(safeSlice(line, 17, 20))
	atom.ChainID = ' '
	if len(line) > 21 {
		atom.ChainID = line[21]
	}
	atom.Serial = parseInt(strings.TrimSpace(safeSlice(line, 6, 11)))
	if atom.Serial == 0 {
		atom.Serial = fallbackSerial
	}
	atom.ResSeq = parseInt(strings.TrimSpace(safeSlice(line, 22, 26)))
	atom.X = parseFloat(strings.TrimSpace(safeSlice(line, 30, 38)))
	atom.Y = parseFloat(strings.TrimSpace(safeSlice(line, 38, 46)))
	atom.Z = parseFloat(strings.TrimSpace(safeSlice(line, 46, 54)))

	atom.Element = strings.TrimSpace(safeSlice(line, 76, 78))
	if atom.Element == "" {
		atom.Element = inferElement(atom.Name)
	}
	atom.Element = normalizeElement(atom.Element)

	return atom, true
}

func parseConect(line string) []Bond {
	if len(line) <= 6 {
		return nil
	}
	fields := strings.Fields(line[6:])
	if len(fields) < 2 {
		return nil
	}

	from := parseInt(fields[0])
	if from == 0 {
		return nil
	}

	out := make([]Bond, 0, len(fields)-1)
	for _, f := range fields[1:] {
		to := parseInt(f)
		if to == 0 || to == from {
			continue
		}
		out = append(out, Bond{A: min(from, to), B: max(from, to)})
	}
	return out
}

func inferBonds(atoms []Atom) []Bond {
	if len(atoms) < 2 {
		return nil
	}

	type key struct{ X, Y, Z int }
	grid := map[key][]int{}
	for i, a := range atoms {
		k := key{
			X: int(math.Floor(a.X / gridCellSize)),
			Y: int(math.Floor(a.Y / gridCellSize)),
			Z: int(math.Floor(a.Z / gridCellSize)),
		}
		grid[k] = append(grid[k], i)
	}

	seen := map[string]struct{}{}
	var bonds []Bond

	for cell, indices := range grid {
		for _, i := range indices {
			a := atoms[i]
			for dx := -1; dx <= 1; dx++ {
				for dy := -1; dy <= 1; dy++ {
					for dz := -1; dz <= 1; dz++ {
						neighbor := key{X: cell.X + dx, Y: cell.Y + dy, Z: cell.Z + dz}
						for _, j := range grid[neighbor] {
							if j <= i {
								continue
							}
							b := atoms[j]
							if isHydrogen(a.Element) && isHydrogen(b.Element) {
								continue
							}
							dist := distance(a, b)
							if dist > bondThreshold {
								continue
							}
							aSerial, bSerial := a.Serial, b.Serial
							if aSerial == 0 {
								aSerial = i + 1
							}
							if bSerial == 0 {
								bSerial = j + 1
							}
							k := bondKey(aSerial, bSerial)
							if _, exists := seen[k]; exists {
								continue
							}
							seen[k] = struct{}{}
							bonds = append(bonds, Bond{
								A: min(aSerial, bSerial),
								B: max(aSerial, bSerial),
							})
						}
					}
				}
			}
		}
	}

	sort.Slice(bonds, func(i, j int) bool {
		if bonds[i].A != bonds[j].A {
			return bonds[i].A < bonds[j].A
		}
		return bonds[i].B < bonds[j].B
	})
	return bonds
}

func computeBounds(m *Molecule) {
	atoms := m.Frames[0].Atoms
	if len(atoms) == 0 {
		return
	}

	minX, maxX := atoms[0].X, atoms[0].X
	minY, maxY := atoms[0].Y, atoms[0].Y
	minZ, maxZ := atoms[0].Z, atoms[0].Z
	for _, a := range atoms[1:] {
		minX, maxX = math.Min(minX, a.X), math.Max(maxX, a.X)
		minY, maxY = math.Min(minY, a.Y), math.Max(maxY, a.Y)
		minZ, maxZ = math.Min(minZ, a.Z), math.Max(maxZ, a.Z)
	}
	m.MinX, m.MaxX = minX, maxX
	m.MinY, m.MaxY = minY, maxY
	m.MinZ, m.MaxZ = minZ, maxZ
	m.CenterX = (minX + maxX) * 0.5
	m.CenterY = (minY + maxY) * 0.5
	m.CenterZ = (minZ + maxZ) * 0.5
	m.Extent = max(maxX-minX, maxY-minY, maxZ-minZ)
}

func safeSlice(s string, start, end int) string {
	if start >= len(s) {
		return ""
	}
	if end > len(s) {
		end = len(s)
	}
	return s[start:end]
}

func parseInt(s string) int {
	v, _ := strconv.Atoi(strings.TrimSpace(s))
	return v
}

func parseFloat(s string) float64 {
	v, _ := strconv.ParseFloat(strings.TrimSpace(s), 64)
	return v
}

func inferElement(name string) string {
	n := strings.TrimSpace(name)
	if n == "" {
		return "C"
	}
	n = strings.TrimLeftFunc(n, func(r rune) bool {
		return r >= '0' && r <= '9'
	})
	if n == "" {
		return "C"
	}
	runes := []rune(n)
	if len(runes) >= 2 && runes[1] >= 'a' && runes[1] <= 'z' {
		return string(runes[:2])
	}
	return string(runes[0])
}

func normalizeElement(el string) string {
	el = strings.TrimSpace(el)
	if el == "" {
		return "C"
	}
	el = strings.ToLower(el)
	if len(el) == 1 {
		return strings.ToUpper(el)
	}
	return strings.ToUpper(el[:1]) + el[1:2]
}

func bondKey(a, b int) string {
	if a > b {
		a, b = b, a
	}
	return fmt.Sprintf("%d-%d", a, b)
}

func isHydrogen(el string) bool {
	return strings.EqualFold(strings.TrimSpace(el), "H")
}

func distance(a, b Atom) float64 {
	dx := a.X - b.X
	dy := a.Y - b.Y
	dz := a.Z - b.Z
	return math.Sqrt(dx*dx + dy*dy + dz*dz)
}

// parseHelix reads initChainID, initSeqNum, endSeqNum from a HELIX record.
// PDB column indices (0-based): chain=19, startSeq=21-24, endChain=31, endSeq=33-36.
func parseHelix(line string) SSSegment {
	chain := byte(' ')
	if len(line) > 19 {
		chain = line[19]
	}
	return SSSegment{
		Chain:    chain,
		StartSeq: parseInt(safeSlice(line, 21, 25)),
		EndSeq:   parseInt(safeSlice(line, 33, 37)),
		Type:     SSHelix,
	}
}

// parseSheet reads initChainID, initSeqNum, endSeqNum from a SHEET record.
// PDB column indices (0-based): chain=21, startSeq=22-25, endChain=32, endSeq=33-36.
func parseSheet(line string) SSSegment {
	chain := byte(' ')
	if len(line) > 21 {
		chain = line[21]
	}
	return SSSegment{
		Chain:    chain,
		StartSeq: parseInt(safeSlice(line, 22, 26)),
		EndSeq:   parseInt(safeSlice(line, 33, 37)),
		Type:     SSStrand,
	}
}
