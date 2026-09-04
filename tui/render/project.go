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

func Project(v Vec3, canvasW, canvasH int, zoom, panX, panY float64) (int, int) {
	sx := float64(canvasW)/2.0 + (v.X * zoom) + panX
	sy := float64(canvasH)/2.0 - (v.Y * zoom * 0.5) + panY
	return int(math.Round(sx)), int(math.Round(sy))
}
