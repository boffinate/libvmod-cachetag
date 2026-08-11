package main

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestStartPhaseMarkerWaitsForReady(t *testing.T) {
	dir := t.TempDir()
	cfg := config{
		mode:              "load",
		httpTimeout:       1,
		phaseMarkerDir:    dir,
		phaseMarkerPrefix: "handshake",
		phaseRequireReady: true,
	}
	done := make(chan error, 1)
	go func() { done <- startPhaseMarker(cfg, "load") }()

	start := filepath.Join(dir, "handshake.load.start")
	deadline := time.Now().Add(time.Second)
	for {
		if _, err := os.Stat(start); err == nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("start marker was not written")
		}
		time.Sleep(time.Millisecond)
	}
	select {
	case err := <-done:
		t.Fatalf("phase started before ready marker: %v", err)
	case <-time.After(50 * time.Millisecond):
	}
	ready := filepath.Join(dir, "handshake.load.ready")
	if err := os.WriteFile(ready, []byte("event=ready\n"), 0644); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("phase did not start after ready marker")
	}
}
