package graphics

import (
	"encoding/base64"
	"strconv"
)

// Kitty graphics protocol framing: every command is an APC string,
// ESC _ G <keys> ; <payload> ESC \.
const (
	kittyStart = "\x1b_G"
	kittyEnd   = "\x1b\\"
)

// kittyChunk is the largest base64 payload kitty accepts in one command.
const kittyChunk = 4096

// EncodeKitty transmits and places a PNG in one go (a=T, f=100) as image id, scaled
// to cols x rows cells at the cursor. q=2 suppresses every reply: without it
// kitty answers on stdin and Bubble Tea reads the answer as key presses. The
// payload is split into 4096-byte pieces; m=1 says more follow, m=0 ends it.
func EncodeKitty(png []byte, id, cols, rows int) []byte {
	data := base64.StdEncoding.EncodeToString(png)
	out := make([]byte, 0, len(data)+(len(data)/kittyChunk+1)*16+64)
	out = append(out, kittyStart+"a=T,f=100,i="...)
	out = strconv.AppendInt(out, int64(id), 10)
	out = append(out, ",c="...)
	out = strconv.AppendInt(out, int64(cols), 10)
	out = append(out, ",r="...)
	out = strconv.AppendInt(out, int64(rows), 10)
	out = append(out, ",q=2,"...)
	for first := true; first || data != ""; first = false {
		if !first {
			out = append(out, kittyStart...)
		}
		n := min(kittyChunk, len(data))
		if n == len(data) {
			out = append(out, "m=0;"...)
		} else {
			out = append(out, "m=1;"...)
		}
		out = append(out, data[:n]...)
		out = append(out, kittyEnd...)
		data = data[n:]
	}
	return out
}

// KittyDelete removes image id and its placements and frees its data (d=I; lowercase i would keep the data until the id is reused), quietly (q=2).
func KittyDelete(id int) []byte {
	return []byte(kittyStart + "a=d,d=I,i=" + strconv.Itoa(id) + ",q=2" + kittyEnd)
}
