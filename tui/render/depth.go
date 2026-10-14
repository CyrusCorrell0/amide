package render

// DepthCue fades far pixels toward the black canvas. Each lit pixel's colour is
// scaled by floor at the farthest depth up to 1 at the nearest, linearly in depth.
func (pb *PixelBuffer) DepthCue(floor float64) {
	zmin, zmax, lit := 0.0, 0.0, false
	for _, p := range pb.Pixels {
		if !p.HasColor {
			continue
		}
		if !lit {
			zmin, zmax, lit = p.Depth, p.Depth, true
			continue
		}
		zmin, zmax = min(zmin, p.Depth), max(zmax, p.Depth)
	}
	span := zmax - zmin
	if !lit || span < 1e-6 {
		return
	}
	for i := range pb.Pixels {
		p := &pb.Pixels[i]
		if p.HasColor {
			scaleRGB(p, floor+(1-floor)*(p.Depth-zmin)/span)
		}
	}
}

// Outline darkens every lit pixel that has a lit 4-neighbour more than step behind
// it, which draws a one-pixel edge where a near surface overlaps a far one. The mask
// is built first so the test reads the original depths.
func (pb *PixelBuffer) Outline(step, darken float64) {
	w, h := pb.PWidth, pb.PHeight
	mask := make([]bool, len(pb.Pixels))
	behind := func(x, y int, z float64) bool {
		if x < 0 || x >= w || y < 0 || y >= h {
			return false
		}
		n := pb.Pixels[y*w+x]
		return n.HasColor && n.Depth < z-step
	}
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			p := pb.Pixels[y*w+x]
			if !p.HasColor {
				continue
			}
			z := p.Depth
			mask[y*w+x] = behind(x-1, y, z) || behind(x+1, y, z) || behind(x, y-1, z) || behind(x, y+1, z)
		}
	}
	for i, m := range mask {
		if m {
			scaleRGB(&pb.Pixels[i], darken)
		}
	}
}

func scaleRGB(p *PixelCell, f float64) {
	p.R = uint8(float64(p.R) * f)
	p.G = uint8(float64(p.G) * f)
	p.B = uint8(float64(p.B) * f)
}
