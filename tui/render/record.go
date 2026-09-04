package render

import (
	"image"
	"image/color"
	"image/color/palette"
	"image/draw"
	"image/gif"
	"os"

	"github.com/CyrusCorrell0/amide/tui/pdb"
)

// maxRecordFrames caps GIF length so very long trajectories stay a reasonable file
// size; frames beyond this are sampled with an even stride.
const maxRecordFrames = 600

// ToImage rasterises the current contents of the pixel buffer into an RGBA image at
// full pixel resolution (PWidth x PHeight). Pixels without colour take the supplied
// background.
func (pb *PixelBuffer) ToImage(bg color.RGBA) *image.RGBA {
	img := image.NewRGBA(image.Rect(0, 0, pb.PWidth, pb.PHeight))
	for y := 0; y < pb.PHeight; y++ {
		for x := 0; x < pb.PWidth; x++ {
			p := pb.Pixels[y*pb.PWidth+x]
			c := bg
			if p.HasColor {
				c = color.RGBA{R: p.R, G: p.G, B: p.B, A: 0xFF}
			}
			img.SetRGBA(x, y, c)
		}
	}
	return img
}

// RecordParams describes how to render a trajectory to a GIF. The camera fields come
// straight from the viewer so the recording matches what is on screen.
type RecordParams struct {
	Mol         *pdb.Molecule
	Mode        RenderMode
	ShowCartoon bool
	Cfg         SceneConfig
	TermW       int // recording buffer width in terminal cells
	TermH       int // recording buffer height in terminal cells
	FPS         int // playback FPS, controls GIF frame delay
	Background  color.RGBA
}

// RecordTrajectoryGIF renders every frame of the molecule with the given camera and
// writes an animated GIF to path. It allocates its own pixel buffer so it is safe to
// run off the UI goroutine. Only the Go standard library is used.
func RecordTrajectoryGIF(path string, params RecordParams) error {
	frames := params.Mol.Frames
	if len(frames) == 0 {
		return os.ErrInvalid
	}

	// Sample down if the trajectory is longer than the cap.
	stride := 1
	if len(frames) > maxRecordFrames {
		stride = (len(frames) + maxRecordFrames - 1) / maxRecordFrames
	}

	fps := params.FPS
	if fps < 1 {
		fps = 1
	}
	delay := 100 / fps // GIF delay is in hundredths of a second
	if delay < 1 {
		delay = 1
	}

	pbuf := NewPixelBuffer(params.TermW, params.TermH)
	out := &gif.GIF{}

	for i := 0; i < len(frames); i += stride {
		frame := frames[i]
		if params.ShowCartoon {
			RenderCartoonPixel(pbuf, frame, params.Mol.SSSegments, params.Cfg)
		} else {
			RenderFramePixel(pbuf, frame, params.Mode, params.Cfg)
		}

		rgba := pbuf.ToImage(params.Background)
		paletted := image.NewPaletted(rgba.Bounds(), palette.Plan9)
		draw.FloydSteinberg.Draw(paletted, paletted.Bounds(), rgba, image.Point{})

		out.Image = append(out.Image, paletted)
		out.Delay = append(out.Delay, delay)
	}

	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	return gif.EncodeAll(f, out)
}
