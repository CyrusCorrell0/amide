package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestResolveTarget(t *testing.T) {
	dir := t.TempDir()
	file := filepath.Join(dir, "sample.pdb")
	if err := os.WriteFile(file, []byte("END\n"), 0o644); err != nil {
		t.Fatalf("write sample: %v", err)
	}
	missing := filepath.Join(dir, "nope.pdb")

	cwd, err := os.Getwd()
	if err != nil {
		t.Fatalf("getwd: %v", err)
	}

	tests := []struct {
		name     string
		args     []string
		wantDir  string
		wantFile string
		wantErr  bool
	}{
		{name: "no args roots the picker at the cwd", args: nil, wantDir: cwd},
		{name: "a directory roots the picker", args: []string{dir}, wantDir: dir},
		{name: "a file opens with the picker at its parent", args: []string{file}, wantDir: dir, wantFile: file},
		{name: "a missing path is an error", args: []string{missing}, wantErr: true},
		{name: "two paths are an error", args: []string{dir, file}, wantErr: true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			gotDir, gotFile, err := resolveTarget(tt.args)
			if tt.wantErr {
				if err == nil {
					t.Fatalf("resolveTarget(%q) = (%q, %q, nil), want an error", tt.args, gotDir, gotFile)
				}
				return
			}
			if err != nil {
				t.Fatalf("resolveTarget(%q): %v", tt.args, err)
			}
			if gotDir != tt.wantDir || gotFile != tt.wantFile {
				t.Errorf("resolveTarget(%q) = (%q, %q), want (%q, %q)", tt.args, gotDir, gotFile, tt.wantDir, tt.wantFile)
			}
		})
	}
}

func TestResolveTargetMissingPathNamesIt(t *testing.T) {
	missing := filepath.Join(t.TempDir(), "nope.pdb")
	_, _, err := resolveTarget([]string{missing})
	if err == nil {
		t.Fatal("want an error for a missing path")
	}
	if got := err.Error(); !strings.Contains(got, missing) {
		t.Errorf("error %q does not name %q", got, missing)
	}
}
