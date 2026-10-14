package render

import "math"

type Vec3 struct {
	X, Y, Z float64
}

func RotateYX(v Vec3, rotY, rotX float64) Vec3 {
	cy, sy := math.Cos(rotY), math.Sin(rotY)
	cx, sx := math.Cos(rotX), math.Sin(rotX)

	x1 := v.X*cy + v.Z*sy
	z1 := -v.X*sy + v.Z*cy
	y1 := v.Y

	y2 := y1*cx - z1*sx
	z2 := y1*sx + z1*cx
	return Vec3{X: x1, Y: y2, Z: z2}
}

const (
	// perspectiveMin and perspectiveMax bound the size factor so a point near or
	// behind the camera cannot blow up or vanish.
	perspectiveMin = 0.2
	perspectiveMax = 5.0
)

// perspective is the size factor for a point at depth z (larger is nearer):
// CameraDist / (CameraDist - z), clamped. CameraDist 0 is orthographic and gives 1.
func perspective(cfg SceneConfig, z float64) float64 {
	if cfg.CameraDist <= 0 {
		return 1
	}
	d := cfg.CameraDist - z
	if d <= 0 {
		return perspectiveMax
	}
	return min(perspectiveMax, max(perspectiveMin, cfg.CameraDist/d))
}
