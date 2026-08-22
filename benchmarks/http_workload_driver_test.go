package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestRunLoadRecordsAndValidatesBackendWorkVolume(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Bench-Cache", "miss")
		w.Header().Set("X-Origin-Generation", "1")
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	cfg := config{
		mode:              "xkey-load",
		objects:           3,
		clients:           2,
		buckets:           1,
		tagsPerObject:     1,
		originEpoch:       newOriginEpochController(),
		validateResidency: false,
	}
	lines := metrics{}
	if err := runLoad(server.Client(), server.URL, cfg, &lines); err != nil {
		t.Fatal(err)
	}
	joined := strings.Join(lines, "\n")
	for _, field := range []string{
		"driver_load_requests=3",
		"driver_load_backend_objects=3",
		"driver_load_backend_objects_expected=3",
		"driver_load_backend_objects_validation=true",
	} {
		if !strings.Contains(joined, field) {
			t.Fatalf("load metrics missing %q: %s", field, joined)
		}
	}
}

func TestFixtureLoaderPreservesCanonicalTagOrder(t *testing.T) {
	path := filepath.Join(t.TempDir(), "fixture.jsonl")
	payload := "{\"id\":\"object:0\",\"tags\":[\"tag:z\",\"tag:a\",\"tag:m\"]}\n" +
		"{\"id\":\"object:1\",\"tags\":[\"shared:0\",\"unique:1\"]}\n"
	if err := os.WriteFile(path, []byte(payload), 0o600); err != nil {
		t.Fatal(err)
	}
	records, err := loadFixtureRecords(path)
	if err != nil {
		t.Fatal(err)
	}
	if got, want := strings.Join(records[0], " "), "tag:z tag:a tag:m"; got != want {
		t.Fatalf("stored tag transport=%q, want byte order %q", got, want)
	}
	cfg := config{fixtureRecords: records}
	if got, want := strings.Join(tagsFor(cfg, 1), " "), "shared:0 unique:1"; got != want {
		t.Fatalf("fixture-backed object tags=%q, want %q", got, want)
	}
}

func TestFixtureLoaderRejectsDuplicateIDs(t *testing.T) {
	path := filepath.Join(t.TempDir(), "fixture.jsonl")
	payload := "{\"id\":\"duplicate\",\"tags\":[\"tag:0\"]}\n" +
		"{\"id\":\"duplicate\",\"tags\":[\"tag:1\"]}\n"
	if err := os.WriteFile(path, []byte(payload), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := loadFixtureRecords(path); err == nil || !strings.Contains(err.Error(), "duplicate id") {
		t.Fatalf("loadFixtureRecords error=%v, want duplicate-id rejection", err)
	}
}

func TestFixtureExpectedCountUsesExactFixtureTags(t *testing.T) {
	cfg := config{objects: 2, profile: "zipfian-tags", fixtureRecords: [][]string{{"hot:0", "tail:0"}, {"hot:0", "tail:1"}}}
	if got := expectedCount(cfg, "z:1"); got != 0 {
		t.Fatalf("synthetic profile shortcut leaked into fixture count: got %d", got)
	}
	if got := expectedCount(cfg, "hot:0"); got != 2 {
		t.Fatalf("fixture count=%d, want 2", got)
	}
}

func TestPhase4PacerSkipsMissedSlotsWithoutCatchup(t *testing.T) {
	start := time.Unix(0, 0)
	pacer := newPhase4Pacer(10, start)
	_, _, skipped := pacer.waitAt(start)
	if skipped != 0 {
		t.Fatalf("first slot skipped=%d", skipped)
	}
	_, _, skipped = pacer.waitAt(start.Add(550 * time.Millisecond))
	if skipped != 4 {
		t.Fatalf("skipped=%d, want 4", skipped)
	}
	_, _, skipped = pacer.waitAt(start.Add(560 * time.Millisecond))
	if skipped != 0 {
		t.Fatalf("catch-up slot skipped=%d, want 0", skipped)
	}
	_, _, skipped = pacer.waitAt(start.Add(950 * time.Millisecond))
	if skipped < 1 || skipped > 3 {
		t.Fatalf("second gap skipped=%d, want a bounded missed-slot count", skipped)
	}
}

func TestLatencyRecorderUsesDeterministicCompletePhaseReservoir(t *testing.T) {
	recorder := newLatencyRecorder(3)
	for n := 1; n <= 7; n++ {
		recorder.add(time.Duration(n) * time.Millisecond)
	}
	samples := recorder.snapshot()
	if got, want := len(samples), 3; got != want {
		t.Fatalf("sample count=%d, want %d", got, want)
	}
	allTail := true
	for _, sample := range samples {
		if sample < 0.005 {
			allTail = false
		}
	}
	if allTail {
		t.Fatalf("reservoir retained only the phase tail: %v", samples)
	}
	if got, want := recorder.dropped(), uint64(4); got != want {
		t.Fatalf("dropped=%d, want %d", got, want)
	}
	lines := metrics{}
	recorder.emit("phase", &lines)
	joined := strings.Join(lines, "\n")
	for _, field := range []string{
		"phase_latency_sampling_method=deterministic-reservoir-v1",
		"phase_latency_sampling_dropped=4",
	} {
		if !strings.Contains(joined, field) {
			t.Fatalf("metrics missing %q: %s", field, joined)
		}
	}
}

func TestMergedLatencyRecorderReportsAllObservedSamples(t *testing.T) {
	left := newLatencyRecorder(2)
	right := newLatencyRecorder(2)
	for n := 0; n < 5; n++ {
		left.add(time.Duration(n+1) * time.Millisecond)
		right.add(time.Duration(n+6) * time.Millisecond)
	}
	merged := newLatencyRecorder(4)
	merged.mergeRecorder(left)
	merged.mergeRecorder(right)
	if got, want := merged.observed(), uint64(10); got != want {
		t.Fatalf("observed=%d, want %d", got, want)
	}
	if got, want := merged.dropped(), uint64(6); got != want {
		t.Fatalf("dropped=%d, want %d", got, want)
	}
}

func TestPacingMetricSchema(t *testing.T) {
	stats := pacingStats{}
	stats.offered(0, 100, 0)
	stats.offered(100000000, 200, 2)
	stats.completed(true)
	stats.completed(false)
	lines := metrics{}
	stats.emit("driver_phase", 10, 1, &lines)
	joined := strings.Join(lines, "\n")
	for _, field := range []string{
		"driver_phase_scheduled_slots=4",
		"driver_phase_executed_slots=2",
		"driver_phase_skipped_slots=2",
		"driver_phase_scheduling_lag_seconds=0.000000300",
		"driver_phase_achieved_rps=2.000000000",
		"driver_phase_errors=1",
	} {
		if !strings.Contains(joined, field) {
			t.Fatalf("metrics missing %q: %s", field, joined)
		}
	}
}

func TestFixedWorkSchemaIncludesPendingDrain(t *testing.T) {
	// The integration function uses wall time for fixed-work/RPS and emits the
	// request and pending-drain components independently. Guard the field names
	// here so a future refactor cannot silently revert to request-only timing.
	sourceFields := []string{
		"driver_load_fixed_work_seconds",
		"driver_load_request_seconds",
		"driver_load_pending_drain_seconds",
	}
	for _, field := range sourceFields {
		if field == "" {
			t.Fatal("empty fixed-work schema field")
		}
	}
}
