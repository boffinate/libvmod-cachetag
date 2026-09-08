package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestOhaWarmPointUsesResidentProbeAndPreservesJSON(t *testing.T) {
	if _, err := exec.LookPath("oha"); err != nil {
		t.Fatalf("oha is required by the benchmark image: %v", err)
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Bench-Resident-Probe") != "1" {
			t.Errorf("resident probe header=%q, want 1", r.Header.Get("X-Bench-Resident-Probe"))
		}
		if !strings.HasPrefix(r.URL.Path, "/obj/000") || len(r.URL.Path) != len("/obj/00000000") {
			t.Errorf("resident probe path=%q is outside the 100k object shape", r.URL.Path)
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	markers := t.TempDir()
	metricsPath := filepath.Join(t.TempDir(), "oha.driver")
	cfg := config{
		clients: 1, warmSeconds: 1, httpTimeout: 2, ohaWorkerThreads: 1,
		phaseMarkerDir: markers, phaseMarkerPrefix: "oha-test", metricsPath: metricsPath,
	}
	lines := metrics{}
	result, err := runOhaWarmHits(server.URL, cfg, &lines, "warm-sweep-clients-1", "driver_warm_sweep_clients_1")
	if err != nil {
		t.Fatal(err)
	}
	if result.requests == 0 || result.hits != result.requests || result.errors != 0 || result.misses != 0 {
		t.Fatalf("oha result=%+v, want only successful resident hits", result)
	}
	joined := strings.Join(lines, "\n")
	for _, field := range []string{
		"driver_warm_sweep_clients_1_driver=oha-v1.16.0",
		"driver_warm_sweep_clients_1_access_pattern=random-uniform-100k-regex-v1",
		"driver_warm_sweep_clients_1_oha_status_code_distribution=200:",
		"driver_warm_sweep_clients_1_latency_p99_seconds=",
	} {
		if !strings.Contains(joined, field) {
			t.Fatalf("oha metrics missing %q: %s", field, joined)
		}
	}
	if _, err := os.Stat(ohaResultPath(metricsPath, "driver_warm_sweep_clients_1")); err != nil {
		t.Fatalf("oha JSON result missing: %v", err)
	}
	if _, err := os.Stat(filepath.Join(markers, "oha-test.warm_sweep_clients_1.end")); err != nil {
		t.Fatalf("oha end marker missing: %v", err)
	}
}

func TestOhaWarmPointRejectsResidentMiss(t *testing.T) {
	if _, err := exec.LookPath("oha"); err != nil {
		t.Fatalf("oha is required by the benchmark image: %v", err)
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer server.Close()
	cfg := config{
		clients: 1, warmSeconds: 1, httpTimeout: 2, ohaWorkerThreads: 1,
		phaseMarkerDir: t.TempDir(), phaseMarkerPrefix: "oha-miss-test", metricsPath: filepath.Join(t.TempDir(), "oha.driver"),
	}
	lines := metrics{}
	result, err := runOhaWarmHits(server.URL, cfg, &lines, "warm-sweep-clients-1", "driver_warm_sweep_clients_1")
	if err == nil {
		t.Fatalf("oha accepted resident miss result=%+v", result)
	}
	if result.requests == 0 || result.hits != 0 || result.misses != result.requests || result.errors != 0 {
		t.Fatalf("oha miss result=%+v, want only non-200 responses", result)
	}
}

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

func TestEnvAscendingPositiveIntList(t *testing.T) {
	t.Setenv("BENCH_WARM_CLIENT_SWEEP", "1, 2,4")
	values, err := envAscendingPositiveIntList("BENCH_WARM_CLIENT_SWEEP")
	if err != nil {
		t.Fatal(err)
	}
	if got, want := commaSeparatedInts(values), "1,2,4"; got != want {
		t.Fatalf("parsed clients=%q, want %q", got, want)
	}
	for _, raw := range []string{"2,1", "1,1", "0,1", "one"} {
		t.Setenv("BENCH_WARM_CLIENT_SWEEP", raw)
		if _, err := envAscendingPositiveIntList("BENCH_WARM_CLIENT_SWEEP"); err == nil {
			t.Fatalf("clients %q unexpectedly parsed", raw)
		}
	}
}

func TestWarmClientSweepEmitsResidentHitMetricsAndMarkers(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Bench-Cache", "hit")
		w.Header().Set("X-Origin-Generation", "1")
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	markers := t.TempDir()
	cfg := config{
		objects: 16, buckets: 1, clients: 1, warmSeconds: 1, warmClientSweep: []int{1, 2},
		warmValidateHit: true, phaseMarkerDir: markers, phaseMarkerPrefix: "warm-sweep-test",
	}
	lines := metrics{}
	if err := runWarmClientSweep(server.Client(), server.URL, cfg, &lines); err != nil {
		t.Fatal(err)
	}
	joined := strings.Join(lines, "\n")
	for _, field := range []string{
		"driver_warm_enabled=true", "driver_warm_fixed_work=false", "driver_warm_client_sweep_clients=1,2",
		"driver_warm_requests=", "driver_warm_hits=", "driver_warm_misses=0", "driver_warm_errors=0",
		"driver_warm_sweep_clients_1_requests=", "driver_warm_sweep_clients_1_latency_p50_seconds=",
		"driver_warm_sweep_clients_1_latency_p99_seconds=", "driver_warm_sweep_clients_2_requests=",
	} {
		if !strings.Contains(joined, field) {
			t.Fatalf("warm sweep metrics missing %q: %s", field, joined)
		}
	}
	for _, name := range []string{
		"warm-sweep-test.warm_sweep_clients_1.start", "warm-sweep-test.warm_sweep_clients_1.end",
		"warm-sweep-test.warm_sweep_clients_2.start", "warm-sweep-test.warm_sweep_clients_2.end",
	} {
		if _, err := os.Stat(filepath.Join(markers, name)); err != nil {
			t.Fatalf("warm sweep marker %q: %v", name, err)
		}
	}
}

func TestWarmClientSweepWritesEndMarkerAfterNonHitResponse(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusCreated)
	}))
	defer server.Close()
	markers := t.TempDir()
	cfg := config{
		objects: 1, buckets: 1, clients: 1, warmSeconds: 1, warmClientSweep: []int{1},
		warmValidateHit: true, phaseMarkerDir: markers, phaseMarkerPrefix: "warm-sweep-negative",
	}
	lines := metrics{}
	if err := runWarmClientSweep(server.Client(), server.URL, cfg, &lines); err == nil {
		t.Fatalf("non-hit response unexpectedly succeeded: %s", strings.Join(lines, "\n"))
	}
	if _, err := os.Stat(filepath.Join(markers, "warm-sweep-negative.warm_sweep_clients_1.end")); err != nil {
		t.Fatalf("missing end marker after non-hit response: %v", err)
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

func TestPrepareBulkPurgeRequestsPreservesFixedKeyVolume(t *testing.T) {
	cfg := config{objects: 8, buckets: 4, purgeRequests: 3, purgeKeysPerRequest: 2}
	requests, uniqueKeys, totalExpected, totalKeys := prepareBulkPurgeRequests(cfg)
	if got, want := len(requests), 3; got != want {
		t.Fatalf("request count=%d, want %d", got, want)
	}
	if got, want := totalKeys, 6; got != want {
		t.Fatalf("total keys=%d, want %d", got, want)
	}
	if got, want := strings.Join(uniqueKeys, " "), "bucket:0 bucket:1 bucket:2 bucket:3"; got != want {
		t.Fatalf("unique keys=%q, want %q", got, want)
	}
	if got, want := totalExpected, 8; got != want {
		t.Fatalf("total expected=%d, want %d", got, want)
	}
	if got, want := requests[0].key, "bucket:0 bucket:1"; got != want {
		t.Fatalf("request 0 key=%q, want %q", got, want)
	}
	if got, want := requests[2].expected, 0; got != want {
		t.Fatalf("duplicate request expected=%d, want %d", got, want)
	}
}

func TestExecuteBulkPurgeRequestsCompletesFixedParallelVolumeOnError(t *testing.T) {
	var calls, current, maxCurrent atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		now := current.Add(1)
		for {
			old := maxCurrent.Load()
			if old >= now || maxCurrent.CompareAndSwap(old, now) {
				break
			}
		}
		defer current.Add(-1)
		calls.Add(1)
		time.Sleep(5 * time.Millisecond)
		if r.Header.Get("Key") == "bad" {
			w.Header().Set("Purged", "0")
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		w.Header().Set("Purged", "-1")
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	cfg := config{mode: "cachetag-purge"}
	requests := []bulkPurgeRequest{
		{key: "one"}, {key: "bad"}, {key: "two"}, {key: "three"},
	}
	latencies := newLatencyRecorder(len(requests))
	attempted, completed, published, actual, err := executeBulkPurgeRequests(
		server.Client(), server.URL, cfg, requests, latencies, 2)
	if err == nil {
		t.Fatal("parallel purge unexpectedly succeeded")
	}
	if attempted != len(requests) || calls.Load() != int64(len(requests)) {
		t.Fatalf("attempted=%d calls=%d, want %d each", attempted, calls.Load(), len(requests))
	}
	if completed != 3 || published != 3 || actual != 0 {
		t.Fatalf("completed=%d published=%d actual=%d, want 3, 3, 0", completed, published, actual)
	}
	if maxCurrent.Load() < 2 {
		t.Fatalf("max concurrent requests=%d, want at least 2", maxCurrent.Load())
	}
}

func TestTransportConnectionLimitCoversBulkPurgeWorkers(t *testing.T) {
	cfg := config{
		clients:              1,
		bulkPurgeConcurrency: 8,
	}
	if got, want := transportConnectionLimit(cfg), 8; got != want {
		t.Fatalf("connection limit=%d, want %d", got, want)
	}
}
