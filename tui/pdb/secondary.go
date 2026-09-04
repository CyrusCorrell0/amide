package pdb

// SSType classifies a residue's secondary structure.
type SSType int

const (
	SSCoil   SSType = iota // loop / unclassified
	SSHelix                // alpha helix (HELIX record)
	SSStrand               // beta strand (SHEET record)
)

// SSSegment is a contiguous run of residues sharing one secondary structure type.
type SSSegment struct {
	Chain    byte
	StartSeq int
	EndSeq   int
	Type     SSType
}

// SSTypeFor returns the secondary structure type for the given chain and residue
// sequence number, falling back to SSCoil if no segment covers it.
func SSTypeFor(segments []SSSegment, chain byte, resSeq int) SSType {
	for _, s := range segments {
		if s.Chain == chain && resSeq >= s.StartSeq && resSeq <= s.EndSeq {
			return s.Type
		}
	}
	return SSCoil
}
