package main

import (
	"fmt"
	"io"
	"math"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

type config struct {
	host                  string
	port                  int
	objects               int
	mode                  string
	profile               string
	tagsPerObject         int
	purgeKey              string
	metricsPath           string
	buckets               int
	clients               int
	warmSeconds           int
	warmValidateHit       bool
	residencyValidate     int
	churnCycles           int
	httpTimeout           int
	tagUniverse           int
	purgeRequests         int
	purgeKeysPerRequest   int
	purgeValidate         int
	purgeSettleDelay      int
	purgeHitRecheckDelay  int
	allowStaleAfterPurge  bool
	purgeWindowTimeoutMS  int
	purgeWindowWorkers    int
	concurrentSeconds     int
	concurrentReaders     int
	concurrentWriters     int
	concurrentPurgers     int
	concurrentTargetRPS   int
	concurrentPurgeRate   int
	concurrentInsertEvery int
	purgeStormRate        int
	purgeStormDistinct    int
	purgeStormUnknownPct  int
	purgeStormSoftPct     int
	populatedMapEntries   int
	stream1OverlapPreseed int
	stream1OverlapReads   int
	residencySweepSeconds int
	residencySampleMS     int
	phase4PreSeconds      int
	phase4SweepSeconds    int
	phase4PostSeconds     int
	phase4GuardMS         int
	phase5HoldMS          int
	phase5CapPurges       int
	phase5HoldPublication bool
	phase5Shutdown        bool
	phase6PressureBody    int
	phase6QuietSeconds    int
	phase6TTL             string
	phase6BodyBytes       int
	validateResidency     bool
	evictionValidate      int
	disableKeepAlives     bool
	phaseMarkerDir        string
	phaseMarkerPrefix     string
	churnCompactEachCycle bool
	churnGeneration       int
	storageKind           string
	cacheTagPersist       bool
	tagLengthClass        string
	validateTagShape      bool
	originEpoch           *originEpochController
}

type originEpochController struct {
	requestMu sync.RWMutex
	value     atomic.Uint64
}

func newOriginEpochController() *originEpochController {
	controller := &originEpochController{}
	controller.value.Store(1)
	return controller
}

func (c *originEpochController) current() uint64 {
	return c.value.Load()
}

func (c *originEpochController) advance() uint64 {
	c.requestMu.Lock()
	defer c.requestMu.Unlock()
	return c.value.Add(1)
}

func (c *originEpochController) beginRequest() uint64 {
	c.requestMu.RLock()
	return c.value.Load()
}

func (c *originEpochController) endRequest() {
	c.requestMu.RUnlock()
}

type metrics []string

func (m *metrics) add(key string, value any) {
	switch v := value.(type) {
	case float64:
		*m = append(*m, fmt.Sprintf("%s=%.9f", key, v))
	default:
		*m = append(*m, fmt.Sprintf("%s=%v", key, v))
	}
}

func phaseName(name string) string {
	return strings.NewReplacer("-", "_").Replace(name)
}

func beginPhase(lines *metrics, name string) time.Time {
	lines.add("driver_phase", name)
	return time.Now()
}

func recordPhaseSeconds(lines *metrics, name string, start time.Time) {
	lines.add("driver_"+phaseName(name)+"_wall_seconds", time.Since(start).Seconds())
}

type latencyRecorder struct {
	mu      sync.Mutex
	samples []float64
	limit   int
}

func newLatencyRecorder(limit int) *latencyRecorder {
	return &latencyRecorder{limit: limit}
}

func (r *latencyRecorder) add(d time.Duration) {
	r.mu.Lock()
	if len(r.samples) < r.limit {
		r.samples = append(r.samples, d.Seconds())
	}
	r.mu.Unlock()
}

func (r *latencyRecorder) emit(prefix string, lines *metrics) {
	samples := r.snapshot()
	if len(samples) == 0 {
		return
	}
	sort.Float64s(samples)
	lines.add(prefix+"_latency_samples", len(samples))
	lines.add(prefix+"_latency_p50_seconds", percentile(samples, 0.50))
	lines.add(prefix+"_latency_p95_seconds", percentile(samples, 0.95))
	lines.add(prefix+"_latency_p99_seconds", percentile(samples, 0.99))
	lines.add(prefix+"_latency_p999_seconds", percentile(samples, 0.999))
	lines.add(prefix+"_latency_max_seconds", samples[len(samples)-1])
}

func (r *latencyRecorder) snapshot() []float64 {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]float64(nil), r.samples...)
}

func percentile(sorted []float64, p float64) float64 {
	if len(sorted) == 1 {
		return sorted[0]
	}
	pos := p * float64(len(sorted)-1)
	lo := int(math.Floor(pos))
	hi := int(math.Ceil(pos))
	if lo == hi {
		return sorted[lo]
	}
	weight := pos - float64(lo)
	return sorted[lo]*(1-weight) + sorted[hi]*weight
}

func envInt(name string, def int) (int, error) {
	raw := os.Getenv(name)
	if raw == "" {
		return def, nil
	}
	value, err := strconv.Atoi(raw)
	if err != nil || value <= 0 {
		return 0, fmt.Errorf("%s must be positive", name)
	}
	return value, nil
}

func envIntAllowZero(name string, def int) (int, error) {
	raw := os.Getenv(name)
	if raw == "" {
		return def, nil
	}
	value, err := strconv.Atoi(raw)
	if err != nil || value < 0 {
		return 0, fmt.Errorf("%s must be non-negative", name)
	}
	return value, nil
}

func envBool(name string, def bool) (bool, error) {
	raw := os.Getenv(name)
	if raw == "" {
		return def, nil
	}
	switch raw {
	case "1", "true", "TRUE", "yes", "YES", "on", "ON":
		return true, nil
	case "0", "false", "FALSE", "no", "NO", "off", "OFF":
		return false, nil
	default:
		return false, fmt.Errorf("%s must be boolean", name)
	}
}

func boolInt(value bool) int {
	if value {
		return 1
	}
	return 0
}

func envPercent(name string, def int) (int, error) {
	value, err := envIntAllowZero(name, def)
	if err != nil {
		return 0, err
	}
	if value > 100 {
		return 0, fmt.Errorf("%s must be in 0..100", name)
	}
	return value, nil
}

func usageSeconds(tv syscall.Timeval) float64 {
	return float64(tv.Sec) + float64(tv.Usec)/1_000_000
}

func getUsage() syscall.Rusage {
	var usage syscall.Rusage
	_ = syscall.Getrusage(syscall.RUSAGE_SELF, &usage)
	return usage
}

func makeBaseURL(host string, port int) string {
	host = strings.TrimPrefix(strings.TrimSuffix(host, "]"), "[")
	return "http://" + net.JoinHostPort(host, strconv.Itoa(port))
}

func effectiveTagsPerObject(profile string, configured int) int {
	switch profile {
	case "single-shared-tag", "single-unique-tag":
		return 1
	case "ten-unique-tags", "five-unique-five-shared":
		return 10
	default:
		return configured
	}
}

func base36(value int) string {
	return strconv.FormatInt(int64(value), 36)
}

func compactCutoverTag(prefix string, obj int, slot int, tagsPerObject int) string {
	if tagsPerObject <= 0 {
		tagsPerObject = 1
	}
	return fmt.Sprintf("%s%s", prefix, base36(obj*tagsPerObject+slot))
}

func cutoverTag(cfg config, kind string, obj int, slot int) string {
	switch cfg.tagLengthClass {
	case "short":
		switch kind {
		case "unique":
			return compactCutoverTag("u", obj, slot, cfg.tagsPerObject)
		case "shared":
			return fmt.Sprintf("s%x", slot)
		default:
			return compactCutoverTag("m", obj, slot, cutoverMixedUniqueCount(cfg.tagsPerObject))
		}
	case "long":
		switch kind {
		case "unique":
			return fmt.Sprintf("benchmark-long-cachetag-unique-object-%010d-slot-%02d-edge", obj, slot)
		case "shared":
			return fmt.Sprintf("benchmark-long-cachetag-shared-slot-%02d-global-edge", slot)
		default:
			return fmt.Sprintf("benchmark-long-cachetag-mixed-object-%010d-slot-%02d-edge", obj, slot)
		}
	default:
		switch kind {
		case "unique":
			return fmt.Sprintf("bench-default-unique-object-%010d-slot-%02d", obj, slot)
		case "shared":
			return fmt.Sprintf("bench-default-shared-slot-%02d-global", slot)
		default:
			return fmt.Sprintf("bench-default-mixed-object-%010d-slot-%02d", obj, slot)
		}
	}
}

func cutoverMixedUniqueCount(limit int) int {
	if limit <= 1 {
		return 0
	}
	return limit / 2
}

func profileUsesCutoverTagClass(profile string) bool {
	switch profile {
	case "cutover-mostly-unique", "cutover-mostly-shared", "cutover-mixed":
		return true
	default:
		return false
	}
}

func cutoverTagLengthBounds(tagLengthClass string) (int, int) {
	switch tagLengthClass {
	case "short":
		return 1, 8
	case "long":
		return 50, math.MaxInt32
	default:
		return 20, 50
	}
}

func expectedCutoverSampleUniqueTags(profile string, samples int, tagsPerObject int) (int, bool) {
	switch profile {
	case "cutover-mostly-unique":
		return samples * tagsPerObject, true
	case "cutover-mostly-shared":
		return tagsPerObject, true
	case "cutover-mixed":
		uniquePerObject := cutoverMixedUniqueCount(tagsPerObject)
		sharedPerObject := tagsPerObject - uniquePerObject
		return samples*uniquePerObject + sharedPerObject, true
	default:
		return 0, false
	}
}

func tagShapeSampleObjectIDs(objects int) []int {
	samples := objects
	if samples > 1024 {
		samples = 1024
	}
	objectIDs := make([]int, 0, samples+1)
	for obj := 0; obj < samples; obj++ {
		objectIDs = append(objectIDs, obj)
	}
	if objects > samples {
		objectIDs = append(objectIDs, objects-1)
	}
	return objectIDs
}

func defaultPurgeKey(cfg config) string {
	switch cfg.profile {
	case "uniform-tags":
		return "u:0"
	case "zipfian-tags":
		return "z:1"
	case "cms-entity-list":
		return "list:frontpage"
	case "extreme-high-fanout", "explicit-purge", "concurrent", "purge-storm", "purged-cold-residency", "populated-map-warm", "phase4-sweep-latency", "phase4-refill-control", "phase5-held-short", "phase5-held-multi", "phase5-held-cap", "phase5-nohold-short", "phase5-nohold-multi", "phase5-nohold-cap":
		return "site"
	case "phase6-fill-drain":
		return "phase6:full:0"
	case "low-fanout-unique":
		return "group:0"
	case "single-shared-tag":
		return "hot:global"
	case "single-unique-tag", "ten-unique-tags":
		return "u0:0"
	case "five-unique-five-shared":
		return "shared:0"
	case "cutover-mostly-unique":
		return cutoverTag(cfg, "unique", 0, 0)
	case "cutover-mostly-shared", "cutover-mixed":
		return cutoverTag(cfg, "shared", 0, 0)
	case "bulk-purge-bursts":
		return "bucket:0"
	default:
		return "site"
	}
}

func modeIsCachetag(mode string) bool {
	return strings.HasPrefix(mode, "cachetag-")
}

func modeIsNoindex(mode string) bool {
	return strings.HasPrefix(mode, "noindex-") || mode == "noindex"
}

func profileIsRotatingChurn(profile string) bool {
	switch profile {
	case "rotating-tag-churn", "rotating-tag-churn-deterministic-full", "rotating-tag-churn-deterministic-incremental":
		return true
	default:
		return false
	}
}

func profileIsDeterministicChurn(profile string) bool {
	switch profile {
	case "rotating-tag-churn-deterministic-full", "rotating-tag-churn-deterministic-incremental", "phase6-fill-drain":
		return true
	default:
		return false
	}
}

func profileIsIncrementalChurn(profile string) bool {
	return profile == "rotating-tag-churn-deterministic-incremental"
}

func profileIsPhase6(profile string) bool {
	return profile == "phase6-fill-drain"
}

func profileUsesFellowDirectResidentZero(cfg config) bool {
	return cfg.storageKind == "fellow" && cfg.cacheTagPersist
}

func minInt(a int, b int) int {
	if a < b {
		return a
	}
	return b
}

func rotatingChurnGlobalKeys(cfg config) int {
	switch {
	case cfg.tagsPerObject <= 0:
		return 0
	case cfg.tagsPerObject == 1:
		return 1
	default:
		return 2
	}
}

func rotatingChurnKeysPerGeneration(cfg config) int {
	total := 0
	if cfg.tagsPerObject >= 3 {
		total++
	}
	if cfg.tagsPerObject >= 4 {
		total += minInt(cfg.objects, cfg.buckets)
	}
	if cfg.tagsPerObject >= 5 {
		total += cfg.objects
	}
	if cfg.tagsPerObject >= 6 {
		total += cfg.objects
	}
	if cfg.tagsPerObject > 6 {
		total += (cfg.tagsPerObject - 6) * minInt(cfg.objects, 100)
	}
	return total
}

func rotatingChurnExpectedKeysTotal(cfg config) int {
	return rotatingChurnGlobalKeys(cfg) + cfg.churnCycles*rotatingChurnKeysPerGeneration(cfg)
}

func rotatingChurnExpectedLiveGenerationKeys(cfg config) int {
	return rotatingChurnGlobalKeys(cfg) + rotatingChurnKeysPerGeneration(cfg)
}

func modePhase(mode string) string {
	for _, prefix := range []string{
		"cachetag-",
		"xkey-",
		"noindex-",
	} {
		if strings.HasPrefix(mode, prefix) {
			return strings.TrimPrefix(mode, prefix)
		}
	}
	return mode
}

func parseConfig() (config, error) {
	if len(os.Args) != 9 {
		return config{}, fmt.Errorf("usage: http_workload_driver HOST PORT OBJECTS MODE PROFILE TAGS_PER_OBJECT PURGE_KEY METRICS_PATH")
	}
	port, err := strconv.Atoi(os.Args[2])
	if err != nil || port <= 0 {
		return config{}, fmt.Errorf("PORT must be positive")
	}
	objects, err := strconv.Atoi(os.Args[3])
	if err != nil || objects <= 0 {
		return config{}, fmt.Errorf("OBJECTS must be positive")
	}
	tagsPerObject, err := strconv.Atoi(os.Args[6])
	if err != nil || tagsPerObject < 0 {
		return config{}, fmt.Errorf("TAGS_PER_OBJECT must be non-negative")
	}
	tagsPerObject = effectiveTagsPerObject(os.Args[5], tagsPerObject)
	buckets, err := envInt("BENCH_BUCKETS", 1024)
	if err != nil {
		return config{}, err
	}
	clients, err := envInt("BENCH_CLIENTS", 1)
	if err != nil {
		return config{}, err
	}
	warmSeconds, err := envIntAllowZero("BENCH_WARM_SECONDS", 5)
	if err != nil {
		return config{}, err
	}
	warmValidateHit, err := envBool("BENCH_WARM_VALIDATE_HIT", true)
	if err != nil {
		return config{}, err
	}
	residencyValidate, err := envIntAllowZero("BENCH_RESIDENCY_VALIDATE_OBJECTS", 0)
	if err != nil {
		return config{}, err
	}
	profile := os.Args[5]
	churnDefault := 3
	if profileIsPhase6(profile) {
		churnDefault = 10
	}
	churnCycles, err := envInt("CHURN_CYCLES", churnDefault)
	if err != nil {
		return config{}, err
	}
	if profileIsPhase6(profile) && churnCycles < 10 {
		return config{}, fmt.Errorf("phase6-fill-drain requires CHURN_CYCLES >= 10")
	}
	httpTimeout, err := envInt("BENCH_HTTP_TIMEOUT", 30)
	if err != nil {
		return config{}, err
	}
	tagUniverse, err := envInt("BENCH_TAG_UNIVERSE", 10000)
	if err != nil {
		return config{}, err
	}
	purgeRequests, err := envInt("BENCH_PURGE_REQUESTS", 100)
	if err != nil {
		return config{}, err
	}
	purgeKeysPerRequest, err := envInt("BENCH_PURGE_KEYS_PER_REQUEST", 10)
	if err != nil {
		return config{}, err
	}
	purgeValidate, err := envInt("BENCH_PURGE_VALIDATE_OBJECTS", 1000)
	if err != nil {
		return config{}, err
	}
	purgeSettleDelay, err := envIntAllowZero("BENCH_PURGE_SETTLE_MS", -1)
	if err != nil {
		return config{}, err
	}
	if purgeSettleDelay < 0 {
		purgeSettleDelay, err = envIntAllowZero("BENCH_PURGE_VALIDATION_DELAY_MS", 1000)
		if err != nil {
			return config{}, err
		}
	}
	purgeHitRecheckDelay, err := envIntAllowZero("BENCH_PURGE_HIT_RECHECK_DELAY_MS", 0)
	if err != nil {
		return config{}, err
	}
	allowStaleAfterPurge, err := envBool("BENCH_ALLOW_STALE_AFTER_PURGE", false)
	if err != nil {
		return config{}, err
	}
	purgeWindowTimeoutMS, err := envIntAllowZero("BENCH_PURGE_WINDOW_TIMEOUT_MS", 5000)
	if err != nil {
		return config{}, err
	}
	purgeWindowWorkers, err := envIntAllowZero("BENCH_PURGE_WINDOW_CONCURRENCY", 0)
	if err != nil {
		return config{}, err
	}
	concurrentSeconds, err := envInt("BENCH_CONCURRENT_SECONDS", 30)
	if err != nil {
		return config{}, err
	}
	concurrentPurgeRate, err := envInt("BENCH_CONCURRENT_PURGE_RATE", 5)
	if err != nil {
		return config{}, err
	}
	concurrentInsertEvery, err := envInt("BENCH_CONCURRENT_INSERT_EVERY", 5)
	if err != nil {
		return config{}, err
	}
	if concurrentInsertEvery < 2 {
		return config{}, fmt.Errorf("BENCH_CONCURRENT_INSERT_EVERY must be at least 2")
	}
	defaultWriters := clients / (concurrentInsertEvery - 1)
	if defaultWriters < 1 {
		defaultWriters = 1
	}
	concurrentReaders, err := envInt("BENCH_CONCURRENT_READERS", clients)
	if err != nil {
		return config{}, err
	}
	concurrentWriters, err := envIntAllowZero("BENCH_CONCURRENT_WRITERS", defaultWriters)
	if err != nil {
		return config{}, err
	}
	concurrentPurgers, err := envIntAllowZero("BENCH_CONCURRENT_PURGERS", 1)
	if err != nil {
		return config{}, err
	}
	if modeIsNoindex(os.Args[4]) {
		concurrentPurgers = 0
	}
	if concurrentReaders+concurrentWriters < 1 {
		return config{}, fmt.Errorf("BENCH_CONCURRENT_READERS + BENCH_CONCURRENT_WRITERS must be positive")
	}
	concurrentTargetRPS, err := envIntAllowZero("BENCH_CONCURRENT_TARGET_RPS", 0)
	if err != nil {
		return config{}, err
	}
	purgeStormRate, err := envInt("BENCH_PURGE_STORM_RATE", concurrentPurgeRate)
	if err != nil {
		return config{}, err
	}
	purgeStormDistinct, err := envInt("BENCH_PURGE_STORM_DISTINCT", 100000)
	if err != nil {
		return config{}, err
	}
	purgeStormUnknownPct, err := envPercent("BENCH_PURGE_STORM_UNKNOWN_PERCENT", 100)
	if err != nil {
		return config{}, err
	}
	purgeStormSoftPct, err := envPercent("BENCH_PURGE_STORM_SOFT_PERCENT", 0)
	if err != nil {
		return config{}, err
	}
	populatedMapEntries, err := envIntAllowZero("BENCH_POPULATED_MAP_ENTRIES", 1000)
	if err != nil {
		return config{}, err
	}
	stream1OverlapPreseed, err := envInt("BENCH_STREAM1_OVERLAP_PRESEED_ENTRIES", 100000)
	if err != nil {
		return config{}, err
	}
	stream1OverlapReads, err := envInt("BENCH_STREAM1_OVERLAP_READS", 50000)
	if err != nil {
		return config{}, err
	}
	residencySweepSeconds, err := envInt("BENCH_RESIDENCY_SWEEP_SECONDS", concurrentSeconds)
	if err != nil {
		return config{}, err
	}
	residencySampleMS, err := envInt("BENCH_RESIDENCY_SAMPLE_MS", 1000)
	if err != nil {
		return config{}, err
	}
	phase4PreSeconds, err := envInt("BENCH_PHASE4_PRE_SECONDS", 5)
	if err != nil {
		return config{}, err
	}
	phase4SweepSeconds, err := envInt("BENCH_PHASE4_SWEEP_SECONDS", 5)
	if err != nil {
		return config{}, err
	}
	phase4PostSeconds, err := envInt("BENCH_PHASE4_POST_SECONDS", 5)
	if err != nil {
		return config{}, err
	}
	phase4GuardMS, err := envIntAllowZero("BENCH_PHASE4_ATTRIBUTION_GUARD_MS", 10)
	if err != nil {
		return config{}, err
	}
	defaultPhase5HoldMS := 3000
	switch profile {
	case "phase5-held-short", "phase5-nohold-short":
		defaultPhase5HoldMS = 500
	case "phase5-held-shutdown":
		defaultPhase5HoldMS = 3000
	case "phase5-held-cap", "phase5-nohold-cap":
		defaultPhase5HoldMS = 6000
	}
	phase5HoldMS, err := envInt("BENCH_PHASE5_HOLD_MS", defaultPhase5HoldMS)
	if err != nil {
		return config{}, err
	}
	defaultPhase5CapPurges := 0
	if profile == "phase5-held-cap" || profile == "phase5-nohold-cap" {
		defaultPhase5CapPurges = 256
	}
	phase5CapPurges, err := envIntAllowZero("BENCH_PHASE5_CAP_PURGES", defaultPhase5CapPurges)
	if err != nil {
		return config{}, err
	}
	phase6PressureBody, err := envInt("BENCH_PHASE6_PRESSURE_BODY_BYTES", 4096)
	if err != nil {
		return config{}, err
	}
	phase6QuietSeconds, err := envInt("BENCH_PHASE6_QUIET_SECONDS", 6)
	if err != nil {
		return config{}, err
	}
	if profileIsPhase6(profile) {
		if phase6PressureBody <= 0 {
			return config{}, fmt.Errorf("BENCH_PHASE6_PRESSURE_BODY_BYTES must be positive")
		}
		if phase6QuietSeconds < 5 {
			return config{}, fmt.Errorf("BENCH_PHASE6_QUIET_SECONDS must be at least 5")
		}
	}
	validateResidency, err := envBool("BENCH_VALIDATE_RESIDENCY", true)
	if err != nil {
		return config{}, err
	}
	evictionValidate, err := envInt("BENCH_EVICTION_VALIDATE_OBJECTS", 1000)
	if err != nil {
		return config{}, err
	}
	disableKeepAlives, err := envBool("BENCH_HTTP_DISABLE_KEEPALIVES", false)
	if err != nil {
		return config{}, err
	}
	churnCompactEachCycle, err := envBool("CACHE_TAG_CHURN_COMPACT_EACH_CYCLE", false)
	if err != nil {
		return config{}, err
	}
	if profileIsIncrementalChurn(os.Args[5]) {
		churnCompactEachCycle = true
	}
	storageKind := os.Getenv("BENCH_STORAGE_KIND")
	if storageKind == "" {
		storageKind = "default"
	}
	cacheTagPersist := false
	switch os.Getenv("BENCH_CACHE_TAG_PERSIST") {
	case "1", "true", "TRUE", "yes", "YES", "on", "ON":
		cacheTagPersist = true
	case "", "0", "false", "FALSE", "no", "NO", "off", "OFF", "auto":
		cacheTagPersist = false
	default:
		return config{}, fmt.Errorf("BENCH_CACHE_TAG_PERSIST must be boolean or auto")
	}
	tagLengthClass := os.Getenv("BENCH_TAG_LENGTH_CLASS")
	if tagLengthClass == "" {
		tagLengthClass = "default"
	}
	switch tagLengthClass {
	case "short", "default", "long":
	default:
		return config{}, fmt.Errorf("BENCH_TAG_LENGTH_CLASS must be short, default, or long")
	}
	validateTagShape, err := envBool("BENCH_VALIDATE_TAG_SHAPE", false)
	if err != nil {
		return config{}, err
	}
	purgeKey := os.Args[7]
	return config{
		host:                  os.Args[1],
		port:                  port,
		objects:               objects,
		mode:                  os.Args[4],
		profile:               os.Args[5],
		tagsPerObject:         tagsPerObject,
		purgeKey:              purgeKey,
		metricsPath:           os.Args[8],
		buckets:               buckets,
		clients:               clients,
		warmSeconds:           warmSeconds,
		warmValidateHit:       warmValidateHit,
		residencyValidate:     residencyValidate,
		churnCycles:           churnCycles,
		httpTimeout:           httpTimeout,
		tagUniverse:           tagUniverse,
		purgeRequests:         purgeRequests,
		purgeKeysPerRequest:   purgeKeysPerRequest,
		purgeValidate:         purgeValidate,
		purgeSettleDelay:      purgeSettleDelay,
		purgeHitRecheckDelay:  purgeHitRecheckDelay,
		allowStaleAfterPurge:  allowStaleAfterPurge,
		purgeWindowTimeoutMS:  purgeWindowTimeoutMS,
		purgeWindowWorkers:    purgeWindowWorkers,
		concurrentSeconds:     concurrentSeconds,
		concurrentReaders:     concurrentReaders,
		concurrentWriters:     concurrentWriters,
		concurrentPurgers:     concurrentPurgers,
		concurrentTargetRPS:   concurrentTargetRPS,
		concurrentPurgeRate:   concurrentPurgeRate,
		concurrentInsertEvery: concurrentInsertEvery,
		purgeStormRate:        purgeStormRate,
		purgeStormDistinct:    purgeStormDistinct,
		purgeStormUnknownPct:  purgeStormUnknownPct,
		purgeStormSoftPct:     purgeStormSoftPct,
		populatedMapEntries:   populatedMapEntries,
		stream1OverlapPreseed: stream1OverlapPreseed,
		stream1OverlapReads:   stream1OverlapReads,
		residencySweepSeconds: residencySweepSeconds,
		residencySampleMS:     residencySampleMS,
		phase4PreSeconds:      phase4PreSeconds,
		phase4SweepSeconds:    phase4SweepSeconds,
		phase4PostSeconds:     phase4PostSeconds,
		phase4GuardMS:         phase4GuardMS,
		phase5HoldMS:          phase5HoldMS,
		phase5CapPurges:       phase5CapPurges,
		phase5HoldPublication: strings.HasPrefix(profile, "phase5-held-"),
		phase5Shutdown:        profile == "phase5-held-shutdown",
		phase6PressureBody:    phase6PressureBody,
		phase6QuietSeconds:    phase6QuietSeconds,
		validateResidency:     validateResidency,
		evictionValidate:      evictionValidate,
		disableKeepAlives:     disableKeepAlives,
		phaseMarkerDir:        os.Getenv("BENCH_PHASE_MARKER_DIR"),
		phaseMarkerPrefix:     os.Getenv("BENCH_PHASE_MARKER_PREFIX"),
		churnCompactEachCycle: churnCompactEachCycle,
		storageKind:           storageKind,
		cacheTagPersist:       cacheTagPersist,
		tagLengthClass:        tagLengthClass,
		validateTagShape:      validateTagShape,
	}, nil
}

func writePhaseMarker(cfg config, phase string, event string) error {
	if cfg.phaseMarkerDir == "" {
		return nil
	}
	prefix := cfg.phaseMarkerPrefix
	if prefix == "" {
		prefix = phaseName(cfg.mode)
	}
	if err := os.MkdirAll(cfg.phaseMarkerDir, 0755); err != nil {
		return err
	}
	path := filepath.Join(cfg.phaseMarkerDir, fmt.Sprintf("%s.%s.%s", prefix, phaseName(phase), event))
	body := fmt.Sprintf(
		"time_unix_nano=%d\npid=%d\nmode=%s\nprofile=%s\nphase=%s\nevent=%s\n",
		time.Now().UnixNano(),
		os.Getpid(),
		cfg.mode,
		cfg.profile,
		phase,
		event,
	)
	return os.WriteFile(path, []byte(body), 0644)
}

func tagsFor(cfg config, obj int) []string {
	limit := cfg.tagsPerObject
	if limit <= 0 {
		return nil
	}
	add := func(tags []string, tag string) []string {
		if len(tags) >= limit {
			return tags
		}
		return append(tags, tag)
	}
	tags := make([]string, 0, limit)
	switch cfg.profile {
	case "uniform-tags":
		for n := 0; n < limit; n++ {
			tags = append(tags, fmt.Sprintf("u:%d", (obj*limit+n)%cfg.tagUniverse))
		}
	case "zipfian-tags":
		tags = add(tags, "z:1")
		for n := 2; len(tags) < limit; n++ {
			if obj%n == 0 && n <= 16 {
				tags = add(tags, fmt.Sprintf("z:%d", n))
			} else {
				tags = add(tags, fmt.Sprintf("z:%d", 1000+((obj+n)%cfg.tagUniverse)))
			}
		}
	case "cms-entity-list":
		if obj%20 == 0 {
			tags = add(tags, "list:frontpage")
		} else {
			tags = add(tags, fmt.Sprintf("list:category:%d", obj%32))
		}
		tags = add(tags, "site")
		tags = add(tags, fmt.Sprintf("node:%d", obj))
		tags = add(tags, fmt.Sprintf("user:%d", obj%1000))
		tags = add(tags, fmt.Sprintf("taxonomy:%d", obj%64))
		tags = add(tags, fmt.Sprintf("route:/obj/%08d", obj))
		tags = add(tags, fmt.Sprintf("tenant:%d", obj%4))
	case "extreme-high-fanout":
		tags = add(tags, "site")
		tags = add(tags, fmt.Sprintf("tenant:%d", obj%2))
		tags = add(tags, "frontpage")
		tags = add(tags, fmt.Sprintf("bucket:%d", obj%cfg.buckets))
		for n := 5; len(tags) < limit; n++ {
			tags = add(tags, fmt.Sprintf("shared:%d", n))
		}
	case "low-fanout-unique":
		tags = add(tags, fmt.Sprintf("url:/obj/%08d", obj))
		tags = add(tags, fmt.Sprintf("group:%d", obj/100))
		tags = add(tags, fmt.Sprintf("ns:%d", obj))
		for n := 4; len(tags) < limit; n++ {
			tags = add(tags, fmt.Sprintf("slot%d:%d", n, obj%100))
		}
	case "single-shared-tag":
		tags = add(tags, "hot:global")
	case "single-unique-tag":
		tags = add(tags, fmt.Sprintf("u0:%d", obj))
	case "ten-unique-tags":
		for n := 0; len(tags) < limit; n++ {
			tags = add(tags, fmt.Sprintf("u%d:%d", n, obj))
		}
	case "five-unique-five-shared":
		for n := 0; n < 5 && len(tags) < limit; n++ {
			tags = add(tags, fmt.Sprintf("u%d:%d", n, obj))
		}
		for n := 0; len(tags) < limit; n++ {
			tags = add(tags, fmt.Sprintf("shared:%d", n))
		}
	case "cutover-mostly-unique":
		for n := 0; len(tags) < limit; n++ {
			tags = add(tags, cutoverTag(cfg, "unique", obj, n))
		}
	case "cutover-mostly-shared":
		for n := 0; len(tags) < limit; n++ {
			tags = add(tags, cutoverTag(cfg, "shared", 0, n))
		}
	case "cutover-mixed":
		unique := cutoverMixedUniqueCount(limit)
		for n := 0; n < unique && len(tags) < limit; n++ {
			tags = add(tags, cutoverTag(cfg, "unique", obj, n))
		}
		for n := 0; len(tags) < limit; n++ {
			tags = add(tags, cutoverTag(cfg, "shared", 0, n))
		}
	case "rotating-tag-churn":
		tags = add(tags, "site")
		tags = add(tags, "tenant:alpha")
		tags = add(tags, fmt.Sprintf("build:%d", cfg.churnGeneration))
		tags = add(tags, fmt.Sprintf("bucket:%d:%d", cfg.churnGeneration, obj%cfg.buckets))
		tags = add(tags, fmt.Sprintf("node:%d:%d", cfg.churnGeneration, obj))
		tags = add(tags, fmt.Sprintf("route:%d:/obj/%08d", cfg.churnGeneration, obj))
		for n := 7; len(tags) < limit; n++ {
			tags = add(tags, fmt.Sprintf("facet%d:%d:%d", n, cfg.churnGeneration, obj%100))
		}
	case "rotating-tag-churn-deterministic-full", "rotating-tag-churn-deterministic-incremental":
		tags = add(tags, "site")
		tags = add(tags, "tenant:alpha")
		tags = add(tags, fmt.Sprintf("build:%d", cfg.churnGeneration))
		tags = add(tags, fmt.Sprintf("bucket:%d:%d", cfg.churnGeneration, obj%cfg.buckets))
		tags = add(tags, fmt.Sprintf("node:%d:%d", cfg.churnGeneration, obj))
		tags = add(tags, fmt.Sprintf("route:%d:/obj/g%d/%08d", cfg.churnGeneration, cfg.churnGeneration, obj))
		for n := 7; len(tags) < limit; n++ {
			tags = add(tags, fmt.Sprintf("facet%d:%d:%d", n, cfg.churnGeneration, obj%100))
		}
	case "phase6-fill-drain":
		tags = add(tags, "site")
		tags = add(tags, fmt.Sprintf("phase6:full:%d", cfg.churnGeneration))
		tags = add(tags, fmt.Sprintf("phase6:soft:%d", cfg.churnGeneration))
		tags = add(tags, fmt.Sprintf("phase6:partial:%d:%d", cfg.churnGeneration, obj%8))
	default:
		tags = add(tags, "site")
		tags = add(tags, "tenant:alpha")
		tags = add(tags, fmt.Sprintf("bucket:%d", obj%cfg.buckets))
		tags = add(tags, "segment:global")
		for n := 5; len(tags) < limit; n++ {
			tags = add(tags, fmt.Sprintf("extra%d:shared", n))
		}
	}
	return tags
}

func isUniformTagKeyCandidate(cfg config, key string) bool {
	return cfg.profile == "uniform-tags" && strings.HasPrefix(key, "u:")
}

func uniformTagKeyIndex(cfg config, key string) (int, bool) {
	if !isUniformTagKeyCandidate(cfg, key) {
		return 0, false
	}
	idx, err := strconv.Atoi(strings.TrimPrefix(key, "u:"))
	if err != nil || idx < 0 || idx >= cfg.tagUniverse {
		return 0, false
	}
	return idx, true
}

func uniformExpectedCount(cfg config, keyIndex int) int {
	if cfg.objects <= 0 || cfg.tagsPerObject <= 0 || cfg.tagUniverse <= 0 {
		return 0
	}
	if cfg.tagsPerObject >= cfg.tagUniverse {
		return cfg.objects
	}
	totalTags := int64(cfg.objects) * int64(cfg.tagsPerObject)
	key := int64(keyIndex)
	if key >= totalTags {
		return 0
	}
	return int(((totalTags - 1 - key) / int64(cfg.tagUniverse)) + 1)
}

func uniformObjectHasKey(cfg config, obj int, keyIndex int) bool {
	if obj < 0 || obj >= cfg.objects || cfg.tagsPerObject <= 0 || cfg.tagUniverse <= 0 {
		return false
	}
	if cfg.tagsPerObject >= cfg.tagUniverse {
		return true
	}
	// Uniform tags are consecutive virtual positions modulo the tag universe.
	start := int64(obj) * int64(cfg.tagsPerObject)
	end := start + int64(cfg.tagsPerObject)
	pos := int64(keyIndex)
	if pos < start {
		step := int64(cfg.tagUniverse)
		pos += ((start - pos + step - 1) / step) * step
	}
	return pos < end
}

func uniformMatchingObjectForSample(cfg config, keyIndex int, sample int, samples int) (int, bool) {
	if samples < 1 {
		samples = 1
	}
	count := uniformExpectedCount(cfg, keyIndex)
	if count < 1 {
		return 0, false
	}
	target := (int64(sample) * int64(count)) / int64(samples)
	if target >= int64(count) {
		target = int64(count - 1)
	}
	if cfg.tagsPerObject >= cfg.tagUniverse {
		return int(target), target < int64(cfg.objects)
	}
	pos := int64(keyIndex) + target*int64(cfg.tagUniverse)
	obj := pos / int64(cfg.tagsPerObject)
	return int(obj), obj < int64(cfg.objects)
}

func expectedCount(cfg config, key string) int {
	if isUniformTagKeyCandidate(cfg, key) {
		keyIndex, ok := uniformTagKeyIndex(cfg, key)
		if !ok {
			return 0
		}
		return uniformExpectedCount(cfg, keyIndex)
	}
	if key == "site" || key == "z:1" || key == "hot:global" {
		return cfg.objects
	}
	if key == "frontpage" {
		return cfg.objects
	}
	if strings.HasPrefix(key, "shared:") {
		return cfg.objects
	}
	if cfg.profile == "cutover-mostly-unique" && key == cutoverTag(cfg, "unique", 0, 0) {
		return 1
	}
	if cfg.profile == "cutover-mostly-shared" && key == cutoverTag(cfg, "shared", 0, 0) {
		return cfg.objects
	}
	if cfg.profile == "cutover-mixed" && key == cutoverTag(cfg, "shared", 0, 0) {
		return cfg.objects
	}
	if key == "list:frontpage" {
		return (cfg.objects + 19) / 20
	}
	if strings.HasPrefix(key, "bucket:") {
		bucket, err := strconv.Atoi(strings.TrimPrefix(key, "bucket:"))
		if err == nil && bucket >= 0 && bucket < cfg.buckets {
			if cfg.objects <= bucket {
				return 0
			}
			return ((cfg.objects - 1 - bucket) / cfg.buckets) + 1
		}
	}
	if strings.HasPrefix(key, "group:") {
		group, err := strconv.Atoi(strings.TrimPrefix(key, "group:"))
		if err == nil && group >= 0 {
			start := group * 100
			if start >= cfg.objects {
				return 0
			}
			remaining := cfg.objects - start
			if remaining < 100 {
				return remaining
			}
			return 100
		}
	}
	count := 0
	for obj := 0; obj < cfg.objects; obj++ {
		for _, tag := range tagsFor(cfg, obj) {
			if tag == key {
				count++
				break
			}
		}
	}
	return count
}

func objectHasKey(cfg config, obj int, key string) bool {
	if isUniformTagKeyCandidate(cfg, key) {
		keyIndex, ok := uniformTagKeyIndex(cfg, key)
		return ok && uniformObjectHasKey(cfg, obj, keyIndex)
	}
	for _, tag := range tagsFor(cfg, obj) {
		if tag == key {
			return true
		}
	}
	return false
}

func matchingObjectForSample(cfg config, key string, sample int, samples int) (int, bool) {
	if samples < 1 {
		samples = 1
	}
	if isUniformTagKeyCandidate(cfg, key) {
		keyIndex, ok := uniformTagKeyIndex(cfg, key)
		if !ok {
			return 0, false
		}
		return uniformMatchingObjectForSample(cfg, keyIndex, sample, samples)
	}
	if key == "site" || key == "z:1" || key == "frontpage" || key == "hot:global" || strings.HasPrefix(key, "shared:") {
		obj := (sample * cfg.objects) / samples
		if obj >= cfg.objects {
			obj = cfg.objects - 1
		}
		return obj, obj >= 0
	}
	if cfg.profile == "cutover-mostly-unique" && key == cutoverTag(cfg, "unique", 0, 0) {
		return 0, cfg.objects > 0
	}
	if (cfg.profile == "cutover-mostly-shared" || cfg.profile == "cutover-mixed") &&
		key == cutoverTag(cfg, "shared", 0, 0) {
		obj := (sample * cfg.objects) / samples
		if obj >= cfg.objects {
			obj = cfg.objects - 1
		}
		return obj, obj >= 0
	}
	if key == "list:frontpage" {
		count := expectedCount(cfg, key)
		if count < 1 {
			return 0, false
		}
		idx := (sample * count) / samples
		if idx >= count {
			idx = count - 1
		}
		obj := idx * 20
		return obj, obj < cfg.objects
	}
	if strings.HasPrefix(key, "bucket:") {
		bucket, err := strconv.Atoi(strings.TrimPrefix(key, "bucket:"))
		count := expectedCount(cfg, key)
		if err != nil || count < 1 {
			return 0, false
		}
		idx := (sample * count) / samples
		if idx >= count {
			idx = count - 1
		}
		obj := bucket + idx*cfg.buckets
		return obj, obj < cfg.objects
	}
	if strings.HasPrefix(key, "group:") {
		group, err := strconv.Atoi(strings.TrimPrefix(key, "group:"))
		count := expectedCount(cfg, key)
		if err != nil || count < 1 {
			return 0, false
		}
		idx := (sample * count) / samples
		if idx >= count {
			idx = count - 1
		}
		obj := group*100 + idx
		return obj, obj < cfg.objects
	}
	found := 0
	target := sample
	for obj := 0; obj < cfg.objects; obj++ {
		if !objectHasKey(cfg, obj, key) {
			continue
		}
		if found == target {
			return obj, true
		}
		found++
	}
	return 0, false
}

type objectResponse struct {
	cacheState       string
	requestedEpoch   uint64
	originGeneration uint64
}

type loadObjectsResult struct {
	requests       int64
	backendObjects int64
	loadSuccesses  int64
}

func objectRequestAtEpoch(client *http.Client, baseURL string, cfg config, obj int, requestedEpoch uint64) (objectResponse, error) {
	path := fmt.Sprintf("/obj/%08d", obj)
	if profileIsDeterministicChurn(cfg.profile) {
		path = fmt.Sprintf("/obj/g%d/%08d", cfg.churnGeneration, obj)
	}
	req, err := http.NewRequest(http.MethodGet, baseURL+path, nil)
	if err != nil {
		return objectResponse{}, err
	}
	req.Header.Set("X-Cache-Tags", strings.Join(tagsFor(cfg, obj), " "))
	req.Header.Set("X-Bucket", strconv.Itoa(obj%cfg.buckets))
	req.Header.Set("X-Bench-Origin-Epoch", strconv.FormatUint(requestedEpoch, 10))
	if profileIsPhase6(cfg.profile) {
		req.Header.Set("X-Bench-Phase6-Generation", strconv.Itoa(cfg.churnGeneration))
		if cfg.phase6TTL != "" {
			req.Header.Set("X-Bench-Phase6-TTL", cfg.phase6TTL)
		}
		if cfg.phase6BodyBytes > 0 {
			req.Header.Set("X-Bench-Body-Bytes", strconv.Itoa(cfg.phase6BodyBytes))
		}
	}
	resp, err := client.Do(req)
	if err != nil {
		return objectResponse{}, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return objectResponse{}, err
	}
	if resp.StatusCode != http.StatusOK {
		return objectResponse{}, fmt.Errorf("request failed object=%d status=%d body=%q", obj, resp.StatusCode, string(body))
	}
	rawGeneration := resp.Header.Get("X-Origin-Generation")
	if rawGeneration == "" {
		return objectResponse{}, fmt.Errorf("request failed object=%d missing X-Origin-Generation header", obj)
	}
	generation, err := strconv.ParseUint(rawGeneration, 10, 64)
	if err != nil || generation == 0 {
		return objectResponse{}, fmt.Errorf("request failed object=%d malformed X-Origin-Generation=%q", obj, rawGeneration)
	}
	return objectResponse{
		cacheState:       resp.Header.Get("X-Bench-Cache"),
		requestedEpoch:   requestedEpoch,
		originGeneration: generation,
	}, nil
}

func objectRequest(client *http.Client, baseURL string, cfg config, obj int) (objectResponse, error) {
	requestedEpoch := uint64(1)
	if cfg.originEpoch != nil {
		requestedEpoch = cfg.originEpoch.current()
	}
	return objectRequestAtEpoch(client, baseURL, cfg, obj, requestedEpoch)
}

func cacheRequest(client *http.Client, baseURL string, cfg config, obj int) error {
	_, err := objectRequest(client, baseURL, cfg, obj)
	return err
}

func pendingRequest(client *http.Client, baseURL string) (int, error) {
	req, err := http.NewRequest(http.MethodGet, baseURL+"/__bench_sync", nil)
	if err != nil {
		return 0, err
	}
	resp, err := client.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return 0, err
	}
	if resp.StatusCode != http.StatusNoContent {
		return 0, fmt.Errorf("sync request failed status=%d body=%q", resp.StatusCode, string(body))
	}
	pending, err := strconv.Atoi(resp.Header.Get("X-Bench-Sync"))
	if err != nil {
		return 0, fmt.Errorf("sync request missing X-Bench-Sync header")
	}
	return pending, nil
}

func objectCountRequest(client *http.Client, baseURL string) (int, error) {
	req, err := http.NewRequest(http.MethodGet, baseURL+"/__bench_objects", nil)
	if err != nil {
		return 0, err
	}
	resp, err := client.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return 0, err
	}
	if resp.StatusCode != http.StatusNoContent {
		return 0, fmt.Errorf("objects request failed status=%d body=%q", resp.StatusCode, string(body))
	}
	objects, err := strconv.Atoi(resp.Header.Get("X-Bench-Objects"))
	if err != nil {
		return 0, fmt.Errorf("objects request missing X-Bench-Objects header")
	}
	return objects, nil
}

func compactRequest(client *http.Client, baseURL string) (int, error) {
	req, err := http.NewRequest("COMPACT", baseURL+"/__bench_compact", nil)
	if err != nil {
		return 0, err
	}
	resp, err := client.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return 0, err
	}
	if resp.StatusCode != http.StatusNoContent && resp.StatusCode != http.StatusOK {
		return 0, fmt.Errorf("compact request failed status=%d body=%q", resp.StatusCode, string(body))
	}
	compacted, err := strconv.Atoi(resp.Header.Get("Compacted"))
	if err != nil {
		return 0, fmt.Errorf("compact request missing Compacted header")
	}
	return compacted, nil
}

func waitForPendingZero(client *http.Client, baseURL string, cfg config) error {
	if !modeIsCachetag(cfg.mode) {
		return nil
	}
	deadline := time.Now().Add(time.Duration(cfg.httpTimeout) * time.Second)
	for {
		pending, err := pendingRequest(client, baseURL)
		if err != nil {
			return err
		}
		if pending == 0 {
			return nil
		}
		if time.Now().After(deadline) {
			return fmt.Errorf("cachetag pending attaches did not drain before timeout; pending=%d", pending)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func waitForObjectCount(client *http.Client, baseURL string, cfg config, target int, lines *metrics, prefix string) error {
	if !modeIsCachetag(cfg.mode) {
		return nil
	}
	timeoutSeconds := cfg.httpTimeout
	if timeoutSeconds < 60 {
		timeoutSeconds = 60
	}
	deadline := time.Now().Add(time.Duration(timeoutSeconds) * time.Second)
	start := time.Now()
	polls := 0
	last := -1
	defer func() {
		lines.add(prefix+"_target_objects", target)
		lines.add(prefix+"_observed_objects", last)
		lines.add(prefix+"_polls", polls)
		lines.add(prefix+"_wall_seconds", time.Since(start).Seconds())
	}()
	for {
		objects, err := objectCountRequest(client, baseURL)
		if err != nil {
			return err
		}
		polls++
		last = objects
		if objects == target {
			return nil
		}
		if time.Now().After(deadline) {
			return fmt.Errorf("cachetag object count did not reach %d before timeout; last=%d", target, objects)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func loadObjectsDetailed(client *http.Client, baseURL string, cfg config, start int, objects int, recorder *latencyRecorder) (loadObjectsResult, error) {
	jobs := make(chan int, cfg.clients*2)
	var result loadObjectsResult
	var firstErr error
	var firstErrMu sync.Mutex
	var stop atomic.Bool
	var wg sync.WaitGroup
	for worker := 0; worker < cfg.clients; worker++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for obj := range jobs {
				if stop.Load() {
					continue
				}
				t0 := time.Now()
				resp, err := objectRequest(client, baseURL, cfg, obj)
				if recorder != nil {
					recorder.add(time.Since(t0))
				}
				if err != nil {
					firstErrMu.Lock()
					if firstErr == nil {
						firstErr = err
					}
					firstErrMu.Unlock()
					stop.Store(true)
					continue
				}
				atomic.AddInt64(&result.requests, 1)
				atomic.AddInt64(&result.loadSuccesses, 1)
				if resp.cacheState != "hit" {
					atomic.AddInt64(&result.backendObjects, 1)
				}
			}
		}()
	}
	for obj := start; obj < start+objects; obj++ {
		if stop.Load() {
			break
		}
		jobs <- obj
	}
	close(jobs)
	wg.Wait()
	firstErrMu.Lock()
	err := firstErr
	firstErrMu.Unlock()
	if err != nil {
		return result, err
	}
	if result.requests != int64(objects) {
		return result, fmt.Errorf("load shortfall requests=%d expected=%d", result.requests, objects)
	}
	return result, nil
}

func loadObjects(client *http.Client, baseURL string, cfg config, start int, objects int, recorder *latencyRecorder) (int64, error) {
	result, err := loadObjectsDetailed(client, baseURL, cfg, start, objects, recorder)
	return result.requests, err
}

func sampledObjects(start int, objects int, samples int) []int {
	if samples <= 0 || samples >= objects {
		result := make([]int, 0, objects)
		for obj := start; obj < start+objects; obj++ {
			result = append(result, obj)
		}
		return result
	}
	result := make([]int, 0, samples)
	seen := make(map[int]bool, samples)
	for n := 0; n < samples; n++ {
		obj := start + (n*objects)/samples
		if obj >= start+objects {
			obj = start + objects - 1
		}
		if !seen[obj] {
			seen[obj] = true
			result = append(result, obj)
		}
	}
	return result
}

func validateResidentHits(client *http.Client, baseURL string, cfg config, start int, objects int, lines *metrics, prefix string) error {
	t0 := time.Now()
	if !cfg.validateResidency {
		lines.add(prefix+"_residency_validation", "disabled")
		lines.add(prefix+"_residency_wall_seconds", time.Since(t0).Seconds())
		return nil
	}
	probes := sampledObjects(start, objects, cfg.residencyValidate)
	var hits int64
	jobs := make(chan int, cfg.clients*2)
	var requests int64
	var firstErr error
	var firstErrMu sync.Mutex
	var stop atomic.Bool
	var wg sync.WaitGroup
	validation := "sample-hit"
	if len(probes) == objects {
		validation = "full-hit"
	}
	defer func() {
		lines.add(prefix+"_residency_validation", validation)
		lines.add(prefix+"_residency_requests", requests)
		lines.add(prefix+"_residency_hits", hits)
		lines.add(prefix+"_residency_sample_limit", cfg.residencyValidate)
		lines.add(prefix+"_residency_wall_seconds", time.Since(t0).Seconds())
	}()
	for worker := 0; worker < cfg.clients; worker++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for obj := range jobs {
				if stop.Load() {
					continue
				}
				resp, err := objectRequest(client, baseURL, cfg, obj)
				atomic.AddInt64(&requests, 1)
				if err == nil && resp.cacheState != "hit" {
					err = fmt.Errorf("residency validation failed object=%d cache=%q", obj, resp.cacheState)
				}
				if err != nil {
					firstErrMu.Lock()
					if firstErr == nil {
						firstErr = err
					}
					firstErrMu.Unlock()
					stop.Store(true)
					continue
				}
				atomic.AddInt64(&hits, 1)
			}
		}()
	}
	for _, obj := range probes {
		if stop.Load() {
			break
		}
		jobs <- obj
	}
	close(jobs)
	wg.Wait()
	firstErrMu.Lock()
	err := firstErr
	firstErrMu.Unlock()
	if err != nil {
		return err
	}
	if requests != int64(len(probes)) {
		return fmt.Errorf("residency validation shortfall requests=%d expected=%d", requests, len(probes))
	}
	return nil
}

func probeCacheStates(client *http.Client, baseURL string, cfg config, start int, objects int, lines *metrics, prefix string) error {
	probes := sampledObjects(start, objects, cfg.residencyValidate)
	var hits int64
	var misses int64
	t0 := time.Now()
	jobs := make(chan int, cfg.clients*2)
	var requests int64
	var firstErr error
	var firstErrMu sync.Mutex
	var stop atomic.Bool
	var wg sync.WaitGroup
	defer func() {
		lines.add(prefix+"_cache_probe", "hit-miss")
		lines.add(prefix+"_probe_requests", requests)
		lines.add(prefix+"_probe_hits", hits)
		lines.add(prefix+"_probe_misses", misses)
		lines.add(prefix+"_probe_sample_limit", cfg.residencyValidate)
		lines.add(prefix+"_probe_wall_seconds", time.Since(t0).Seconds())
	}()
	for worker := 0; worker < cfg.clients; worker++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for obj := range jobs {
				if stop.Load() {
					continue
				}
				resp, err := objectRequest(client, baseURL, cfg, obj)
				atomic.AddInt64(&requests, 1)
				if err != nil {
					firstErrMu.Lock()
					if firstErr == nil {
						firstErr = err
					}
					firstErrMu.Unlock()
					stop.Store(true)
					continue
				}
				switch resp.cacheState {
				case "hit":
					atomic.AddInt64(&hits, 1)
				default:
					atomic.AddInt64(&misses, 1)
				}
			}
		}()
	}
	for _, obj := range probes {
		if stop.Load() {
			break
		}
		jobs <- obj
	}
	close(jobs)
	wg.Wait()
	firstErrMu.Lock()
	err := firstErr
	firstErrMu.Unlock()
	if err != nil {
		return err
	}
	if requests != int64(len(probes)) {
		return fmt.Errorf("cache probe shortfall requests=%d expected=%d", requests, len(probes))
	}
	return nil
}

func validateEvictionMisses(client *http.Client, baseURL string, cfg config, lines *metrics) error {
	t0 := time.Now()
	if !cfg.validateResidency {
		lines.add("driver_eviction_validation", "disabled")
		recordPhaseSeconds(lines, "eviction-validation", t0)
		return nil
	}
	samples := cfg.evictionValidate
	if samples > cfg.objects {
		samples = cfg.objects
	}
	if samples < 1 {
		samples = 1
	}
	hits := 0
	misses := 0
	requests := 0
	defer func() {
		lines.add("driver_eviction_validation", "sample-miss")
		lines.add("driver_eviction_validation_requests", requests)
		lines.add("driver_eviction_validation_hits", hits)
		lines.add("driver_eviction_validation_misses", misses)
		recordPhaseSeconds(lines, "eviction-validation", t0)
	}()
	for n := 0; n < samples; n++ {
		obj := (n * cfg.objects) / samples
		resp, err := objectRequest(client, baseURL, cfg, obj)
		requests++
		if err != nil {
			return err
		}
		switch resp.cacheState {
		case "hit":
			hits++
		default:
			misses++
		}
	}
	if misses == 0 {
		return fmt.Errorf("eviction profile did not observe any cache misses across %d validation probes", samples)
	}
	return nil
}

func sampledObjectsForKey(cfg config, key string) []int {
	expected := expectedCount(cfg, key)
	samples := cfg.purgeValidate
	if samples > expected {
		samples = expected
	}
	if samples < 1 {
		return nil
	}
	seen := make(map[int]bool, samples)
	probes := make([]int, 0, samples)
	for n := 0; n < samples; n++ {
		obj, ok := matchingObjectForSample(cfg, key, n, samples)
		if !ok || seen[obj] {
			continue
		}
		seen[obj] = true
		probes = append(probes, obj)
	}
	return probes
}

type purgeWindowSample struct {
	obj             int
	requests        int
	firstState      string
	firstGeneration uint64
	staleResponses  int
	staleHits       int
	freshAfter      time.Duration
	resolved        bool
}

func generationClassification(response objectResponse) string {
	switch {
	case response.originGeneration < response.requestedEpoch:
		return "older"
	case response.originGeneration > response.requestedEpoch:
		return "newer"
	default:
		return "matching"
	}
}

func isStaleGeneration(response objectResponse) bool {
	return response.originGeneration != response.requestedEpoch
}

func acceptedPurgeEpoch(cfg config, purged int) uint64 {
	if purged == -1 || purged > 0 {
		if cfg.originEpoch != nil {
			return cfg.originEpoch.advance()
		}
	}
	if cfg.originEpoch == nil {
		return 1
	}
	return cfg.originEpoch.current()
}

func purgeWindowWorkerCount(cfg config, samples int) int {
	if samples < 1 {
		return 1
	}
	if cfg.purgeWindowWorkers > 0 {
		if cfg.purgeWindowWorkers > samples {
			return samples
		}
		return cfg.purgeWindowWorkers
	}
	workers := cfg.clients
	if workers < 8 {
		workers = 8
	}
	if workers > 64 {
		workers = 64
	}
	if workers > samples {
		workers = samples
	}
	return workers
}

func emitPurgeWindowBins(prefix string, samples []purgeWindowSample, lines *metrics) {
	lt1ms := 0
	lt10ms := 0
	lt100ms := 0
	lt1s := 0
	ge1s := 0
	for _, sample := range samples {
		if !sample.resolved {
			continue
		}
		ms := sample.freshAfter.Microseconds()
		switch {
		case ms < 1_000:
			lt1ms++
		case ms < 10_000:
			lt10ms++
		case ms < 100_000:
			lt100ms++
		case ms < 1_000_000:
			lt1s++
		default:
			ge1s++
		}
	}
	lines.add(prefix+"_window_objects_lt_1ms", lt1ms)
	lines.add(prefix+"_window_objects_1ms_to_lt_10ms", lt10ms)
	lines.add(prefix+"_window_objects_10ms_to_lt_100ms", lt100ms)
	lines.add(prefix+"_window_objects_100ms_to_lt_1s", lt1s)
	lines.add(prefix+"_window_objects_ge_1s", ge1s)
}

func validatePurgeWindow(client *http.Client, baseURL string, cfg config, key string, lines *metrics, prefix string, requireFirstMiss bool) error {
	probeSetupStartedAt := time.Now()
	expected := expectedCount(cfg, key)
	probes := sampledObjectsForKey(cfg, key)
	lines.add(prefix+"_window_probe_setup_wall_seconds", time.Since(probeSetupStartedAt).Seconds())
	if len(probes) == 0 {
		lines.add(prefix+"_post_publication_validation", "no-matching-objects")
		lines.add(prefix+"_post_publication_validation_requests", 0)
		lines.add(prefix+"_post_publication_ms", cfg.purgeSettleDelay)
		lines.add(prefix+"_window_validation", "no-matching-objects")
		lines.add(prefix+"_window_requests", 0)
		return nil
	}
	if cfg.purgeSettleDelay > 0 {
		time.Sleep(time.Duration(cfg.purgeSettleDelay) * time.Millisecond)
	}
	t0 := time.Now()
	jobs := make(chan int, len(probes))
	results := make(chan purgeWindowSample, len(probes))
	var requests atomic.Int64
	var firstErr error
	var firstErrMu sync.Mutex
	workerCount := purgeWindowWorkerCount(cfg, len(probes))
	timeout := time.Duration(cfg.purgeWindowTimeoutMS) * time.Millisecond
	var wg sync.WaitGroup
	for worker := 0; worker < workerCount; worker++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for obj := range jobs {
				sample := purgeWindowSample{obj: obj}
				probeStartedAt := time.Time{}
				deadline := time.Time{}
				for {
					if probeStartedAt.IsZero() {
						probeStartedAt = time.Now()
						deadline = probeStartedAt.Add(timeout)
					}
					resp, err := objectRequest(client, baseURL, cfg, obj)
					if err != nil {
						firstErrMu.Lock()
						if firstErr == nil {
							firstErr = err
						}
						firstErrMu.Unlock()
						return
					}
					sample.requests++
					requests.Add(1)
					if sample.firstState == "" {
						sample.firstState = resp.cacheState
						sample.firstGeneration = resp.originGeneration
					}
					if !isStaleGeneration(resp) {
						sample.resolved = true
						sample.freshAfter = time.Since(probeStartedAt)
						results <- sample
						break
					}
					sample.staleResponses++
					if resp.cacheState == "hit" {
						sample.staleHits++
					}
					if timeout == 0 || !time.Now().Before(deadline) {
						sample.freshAfter = time.Since(probeStartedAt)
						results <- sample
						break
					}
				}
			}
		}()
	}
	for _, obj := range probes {
		jobs <- obj
	}
	close(jobs)
	wg.Wait()
	close(results)
	firstErrMu.Lock()
	err := firstErr
	firstErrMu.Unlock()
	if err != nil {
		return err
	}
	samples := make([]purgeWindowSample, 0, len(probes))
	freshLatencies := newLatencyRecorder(len(probes))
	hits := 0
	misses := 0
	staleHits := 0
	staleResponses := 0
	staleObjects := 0
	unresolved := 0
	firstHitObject := -1
	firstHitRecheckState := ""
	defer func() {
		validationMode := "sample-generation"
		if requireFirstMiss {
			validationMode = "sample-miss"
		}
		lines.add(prefix+"_post_publication_validation", validationMode)
		lines.add(prefix+"_post_publication_validation_key", key)
		lines.add(prefix+"_post_publication_validation_expected", expected)
		lines.add(prefix+"_post_publication_ms", cfg.purgeSettleDelay)
		lines.add(prefix+"_post_publication_validation_requests", len(probes))
		lines.add(prefix+"_post_publication_validation_hits", hits)
		lines.add(prefix+"_post_publication_validation_misses", misses)
		if firstHitObject >= 0 {
			lines.add(prefix+"_post_publication_validation_first_hit_object", firstHitObject)
			lines.add(prefix+"_post_publication_validation_hit_recheck_delay_ms", cfg.purgeHitRecheckDelay)
			if firstHitRecheckState != "" {
				lines.add(prefix+"_post_publication_validation_first_hit_recheck_state", firstHitRecheckState)
			}
		}
		lines.add(prefix+"_post_publication_validation_wall_seconds", time.Since(t0).Seconds())
		lines.add(prefix+"_window_validation", "sample-generation")
		lines.add(prefix+"_window_key", key)
		lines.add(prefix+"_window_expected", expected)
		lines.add(prefix+"_window_samples", len(probes))
		lines.add(prefix+"_window_requests", requests.Load())
		lines.add(prefix+"_window_allow_stale_after_purge", cfg.allowStaleAfterPurge)
		if cfg.originEpoch != nil {
			lines.add(prefix+"_window_expected_origin_epoch", cfg.originEpoch.current())
		}
		lines.add(prefix+"_window_timeout_ms", cfg.purgeWindowTimeoutMS)
		lines.add(prefix+"_window_workers", workerCount)
		lines.add(prefix+"_window_resolved_objects", len(samples)-unresolved)
		lines.add(prefix+"_window_stale_responses", staleResponses)
		lines.add(prefix+"_window_stale_hits", staleHits)
		lines.add(prefix+"_window_stale_objects", staleObjects)
		lines.add(prefix+"_window_unresolved_objects", unresolved)
		freshLatencies.emit(prefix+"_window_fresh", lines)
		emitPurgeWindowBins(prefix, samples, lines)
	}()
	for sample := range results {
		samples = append(samples, sample)
		switch sample.firstState {
		case "hit":
			hits++
			if firstHitObject < 0 || sample.obj < firstHitObject {
				firstHitObject = sample.obj
			}
		default:
			misses++
		}
		if sample.staleHits > 0 {
			staleHits += sample.staleHits
			staleObjects++
		}
		staleResponses += sample.staleResponses
		if sample.resolved {
			freshLatencies.add(sample.freshAfter)
		} else {
			unresolved++
		}
	}
	if firstHitObject >= 0 && cfg.purgeHitRecheckDelay > 0 {
		time.Sleep(time.Duration(cfg.purgeHitRecheckDelay) * time.Millisecond)
		resp, err := objectRequest(client, baseURL, cfg, firstHitObject)
		if err != nil {
			return err
		}
		firstHitRecheckState = resp.cacheState
	}
	if requireFirstMiss && !cfg.allowStaleAfterPurge && hits > 0 {
		return fmt.Errorf("purge window validation found %d stale hits for key %q across %d probes after %dms settle", hits, key, len(probes), cfg.purgeSettleDelay)
	}
	if len(samples) == 0 {
		return fmt.Errorf("purge window validation produced no samples for key %q", key)
	}
	if !cfg.allowStaleAfterPurge && unresolved > 0 {
		return fmt.Errorf("purge window validation left %d unresolved objects for key %q after %dms timeout", unresolved, key, cfg.purgeWindowTimeoutMS)
	}
	return nil
}

func validatePurgedKeySetMisses(client *http.Client, baseURL string, cfg config, keys []string, lines *metrics, prefix string) error {
	if len(keys) == 0 {
		lines.add(prefix+"_validation", "no-keys")
		lines.add(prefix+"_validation_requests", 0)
		return nil
	}
	samples := cfg.purgeValidate
	if samples < 1 {
		samples = 1
	}
	perKey := samples / len(keys)
	if perKey < 1 {
		perKey = 1
	}
	t0 := time.Now()
	hits := 0
	misses := 0
	seenObjects := make(map[int]bool, samples)
	defer func() {
		lines.add(prefix+"_validation", "sample-miss")
		lines.add(prefix+"_validation_keys", len(keys))
		lines.add(prefix+"_validation_requests", len(seenObjects))
		lines.add(prefix+"_validation_hits", hits)
		lines.add(prefix+"_validation_misses", misses)
		lines.add(prefix+"_validation_wall_seconds", time.Since(t0).Seconds())
	}()
	for _, key := range keys {
		expected := expectedCount(cfg, key)
		keySamples := perKey
		if keySamples > expected {
			keySamples = expected
		}
		for n := 0; n < keySamples; n++ {
			obj, ok := matchingObjectForSample(cfg, key, n, keySamples)
			if !ok || seenObjects[obj] {
				continue
			}
			seenObjects[obj] = true
			resp, err := objectRequest(client, baseURL, cfg, obj)
			if err != nil {
				return err
			}
			switch resp.cacheState {
			case "hit":
				hits++
			default:
				misses++
			}
		}
	}
	if hits > 0 {
		return fmt.Errorf("bulk purge validation found %d stale hits across %d probes", hits, len(seenObjects))
	}
	if misses == 0 {
		return fmt.Errorf("bulk purge validation found no misses across %d probes", len(seenObjects))
	}
	return nil
}

func purgeWithMode(client *http.Client, baseURL string, key string, mode string, expected int, exact bool, allowQueued bool) (int, error) {
	req, err := http.NewRequest("PURGE", baseURL+"/", nil)
	if err != nil {
		return 0, err
	}
	req.Header.Set("Key", key)
	if mode != "" {
		req.Header.Set("X-Bench-Purge-Mode", mode)
	}
	resp, err := client.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return 0, err
	}
	purged, err := strconv.Atoi(resp.Header.Get("Purged"))
	if resp.StatusCode != http.StatusOK || err != nil {
		return purged, fmt.Errorf("purge failed status=%d purged=%q body=%q", resp.StatusCode, resp.Header.Get("Purged"), string(body))
	}
	if allowQueued {
		if purged != -1 {
			return purged, fmt.Errorf("cachetag purge publication result mismatch key=%q purged=%d expected=-1 body=%q", key, purged, string(body))
		}
		return purged, nil
	}
	if exact && purged != expected {
		return purged, fmt.Errorf("purge count mismatch key=%q purged=%d expected=%d body=%q", key, purged, expected, string(body))
	}
	if !exact && (purged < 0 && !(allowQueued && purged == -1)) {
		return purged, fmt.Errorf("purge count error key=%q purged=%d body=%q", key, purged, string(body))
	}
	return purged, nil
}

func purge(client *http.Client, baseURL string, key string, expected int, exact bool, allowQueued bool) (int, error) {
	return purgeWithMode(client, baseURL, key, "", expected, exact, allowQueued)
}

func runLoadObjectsPhase(client *http.Client, baseURL string, cfg config, lines *metrics) error {
	start := beginPhase(lines, "load")
	if err := writePhaseMarker(cfg, "load", "start"); err != nil {
		return err
	}
	requests, err := loadObjects(client, baseURL, cfg, 0, cfg.objects, nil)
	if err == nil {
		err = waitForPendingZero(client, baseURL, cfg)
	}
	seconds := time.Since(start).Seconds()
	markerErr := writePhaseMarker(cfg, "load", "end")
	lines.add("driver_load_wall_seconds", seconds)
	lines.add("driver_load_requests", requests)
	if seconds > 0 {
		lines.add("driver_load_requests_per_second", float64(requests)/seconds)
	}
	if err != nil {
		return err
	}
	return markerErr
}

func runExactLoadObjectsPhase(client *http.Client, baseURL string, cfg config, lines *metrics) error {
	start := beginPhase(lines, "load")
	if err := writePhaseMarker(cfg, "load", "start"); err != nil {
		return err
	}
	result, err := loadObjectsDetailed(client, baseURL, cfg, 0, cfg.objects, nil)
	if err == nil && result.backendObjects != int64(cfg.objects) {
		err = fmt.Errorf("exact load backend objects=%d expected=%d", result.backendObjects, cfg.objects)
	}
	if err == nil {
		err = waitForPendingZero(client, baseURL, cfg)
	}
	seconds := time.Since(start).Seconds()
	markerErr := writePhaseMarker(cfg, "load", "end")
	lines.add("driver_load_wall_seconds", seconds)
	lines.add("driver_load_requests", result.requests)
	lines.add("driver_load_backend_objects", result.backendObjects)
	lines.add("driver_load_backend_objects_expected", cfg.objects)
	lines.add("driver_load_backend_objects_validation", result.backendObjects == int64(cfg.objects))
	if seconds > 0 {
		lines.add("driver_load_requests_per_second", float64(result.requests)/seconds)
	}
	if err != nil {
		return err
	}
	return markerErr
}

func runWarmHits(client *http.Client, baseURL string, cfg config, lines *metrics) error {
	if cfg.warmSeconds <= 0 {
		lines.add("driver_warm_enabled", false)
		return nil
	}
	start := beginPhase(lines, "warm")
	if err := writePhaseMarker(cfg, "warm", "start"); err != nil {
		return err
	}
	deadline := time.Now().Add(time.Duration(cfg.warmSeconds) * time.Second)
	latencies := newLatencyRecorder(200000)
	var requests, hits, misses, errors int64
	var next atomic.Int64
	var firstErr error
	var firstErrMu sync.Mutex
	var stop atomic.Bool
	var wg sync.WaitGroup

	recordErr := func(err error) {
		if err == nil {
			return
		}
		atomic.AddInt64(&errors, 1)
		firstErrMu.Lock()
		if firstErr == nil {
			firstErr = err
		}
		firstErrMu.Unlock()
		stop.Store(true)
	}

	for worker := 0; worker < cfg.clients; worker++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for time.Now().Before(deadline) && !stop.Load() {
				obj := int(next.Add(1)-1) % cfg.objects
				t0 := time.Now()
				resp, err := objectRequest(client, baseURL, cfg, obj)
				latencies.add(time.Since(t0))
				if err != nil {
					recordErr(err)
					continue
				}
				atomic.AddInt64(&requests, 1)
				if resp.cacheState == "hit" {
					atomic.AddInt64(&hits, 1)
				} else {
					atomic.AddInt64(&misses, 1)
					if cfg.warmValidateHit {
						recordErr(fmt.Errorf("warm request returned %q for object %d", resp.cacheState, obj))
					}
				}
			}
		}()
	}
	wg.Wait()
	markerErr := writePhaseMarker(cfg, "warm", "end")

	seconds := time.Since(start).Seconds()
	lines.add("driver_warm_enabled", true)
	lines.add("driver_warm_seconds_requested", cfg.warmSeconds)
	lines.add("driver_warm_validate_hit", cfg.warmValidateHit)
	lines.add("driver_warm_wall_seconds", seconds)
	lines.add("driver_warm_requests", requests)
	if seconds > 0 {
		lines.add("driver_warm_requests_per_second", float64(requests)/seconds)
	}
	lines.add("driver_warm_hits", hits)
	lines.add("driver_warm_misses", misses)
	lines.add("driver_warm_errors", errors)
	latencies.emit("driver_warm", lines)

	firstErrMu.Lock()
	err := firstErr
	firstErrMu.Unlock()
	if err != nil {
		return err
	}
	return markerErr
}

func runLoad(client *http.Client, baseURL string, cfg config, lines *metrics) error {
	if err := runLoadObjectsPhase(client, baseURL, cfg, lines); err != nil {
		return err
	}
	beginPhase(lines, "load-residency")
	if err := validateResidentHits(client, baseURL, cfg, 0, cfg.objects, lines, "driver_load"); err != nil {
		return err
	}
	return runWarmHits(client, baseURL, cfg, lines)
}

func runEviction(client *http.Client, baseURL string, cfg config, lines *metrics) error {
	if err := runLoadObjectsPhase(client, baseURL, cfg, lines); err != nil {
		return err
	}
	beginPhase(lines, "eviction-validation")
	return validateEvictionMisses(client, baseURL, cfg, lines)
}

func runPurge(client *http.Client, baseURL string, cfg config, lines *metrics, exact bool) error {
	expected := expectedCount(cfg, cfg.purgeKey)
	t0 := beginPhase(lines, "purge")
	allowQueued := modeIsCachetag(cfg.mode)
	purged, err := purge(client, baseURL, cfg.purgeKey, expected, exact, allowQueued)
	lines.add("driver_purge_key", cfg.purgeKey)
	lines.add("driver_purge_expected", expected)
	lines.add("driver_purge_exact", exact)
	lines.add("driver_purge_actual", purged)
	lines.add("driver_purge_published", purged == -1)
	lines.add("driver_purge_wall_seconds", time.Since(t0).Seconds())
	if err != nil {
		return err
	}
	newEpoch := acceptedPurgeEpoch(cfg, purged)
	lines.add("driver_purge_origin_epoch", newEpoch)
	return validatePurgeWindow(client, baseURL, cfg, cfg.purgeKey, lines, "driver_purge", true)
}

func runShortTTL(client *http.Client, baseURL string, cfg config, lines *metrics) error {
	var requests int64
	loadSecondsTotal := 0.0
	probeSecondsTotal := 0.0
	compactSecondsTotal := 0.0
	completedCycles := 0
	t0 := time.Now()
	defer func() {
		lines.add("driver_churn_cycles", cfg.churnCycles)
		lines.add("driver_churn_deterministic", profileIsDeterministicChurn(cfg.profile))
		lines.add("driver_churn_compact_each_cycle", cfg.churnCompactEachCycle)
		if profileIsRotatingChurn(cfg.profile) {
			lines.add("driver_churn_expected_keys_total", rotatingChurnExpectedKeysTotal(cfg))
			lines.add("driver_churn_expected_live_generation_keys", rotatingChurnExpectedLiveGenerationKeys(cfg))
		}
		lines.add("driver_short_ttl_completed_cycles", completedCycles)
		lines.add("driver_short_ttl_load_wall_seconds", loadSecondsTotal)
		lines.add("driver_short_ttl_probe_wall_seconds", probeSecondsTotal)
		lines.add("driver_short_ttl_compact_wall_seconds", compactSecondsTotal)
		lines.add("driver_short_ttl_wall_seconds", time.Since(t0).Seconds())
		lines.add("driver_short_ttl_requests", requests)
		lines.add("driver_load_requests", requests)
		if loadSecondsTotal > 0 {
			lines.add("driver_load_requests_per_second", float64(requests)/loadSecondsTotal)
		}
	}()
	for cycle := 0; cycle < cfg.churnCycles; cycle++ {
		cycleCfg := cfg
		cycleCfg.churnGeneration = cycle
		lines.add("driver_short_ttl_cycle", cycle)
		loadStart := beginPhase(lines, "short-ttl-load")
		loadResult, err := loadObjectsDetailed(client, baseURL, cycleCfg, 0, cfg.objects, nil)
		n := loadResult.requests
		loadSeconds := time.Since(loadStart).Seconds()
		loadSecondsTotal += loadSeconds
		requests += n
		lines.add(fmt.Sprintf("driver_cycle_%d_load_requests", cycle), n)
		lines.add(fmt.Sprintf("driver_cycle_%d_backend_objects", cycle), loadResult.backendObjects)
		lines.add(fmt.Sprintf("driver_cycle_%d_backend_objects_expected", cycle), cfg.objects)
		lines.add(fmt.Sprintf("driver_cycle_%d_tagged_objects", cycle), loadResult.backendObjects)
		lines.add(fmt.Sprintf("driver_cycle_%d_load_successes", cycle), loadResult.loadSuccesses)
		lines.add(fmt.Sprintf("driver_cycle_%d_load_wall_seconds", cycle), loadSeconds)
		if loadSeconds > 0 {
			lines.add(fmt.Sprintf("driver_cycle_%d_load_requests_per_second", cycle), float64(n)/loadSeconds)
		}
		if err != nil {
			return err
		}
		if profileIsDeterministicChurn(cfg.profile) && loadResult.backendObjects != int64(cfg.objects) {
			return fmt.Errorf("deterministic churn load was not all-miss cycle=%d backend_objects=%d expected=%d", cycle, loadResult.backendObjects, cfg.objects)
		}
		probeStart := beginPhase(lines, "short-ttl-probe")
		if err := probeCacheStates(client, baseURL, cycleCfg, 0, cfg.objects, lines, fmt.Sprintf("driver_cycle_%d", cycle)); err != nil {
			probeSecondsTotal += time.Since(probeStart).Seconds()
			return err
		}
		probeSecondsTotal += time.Since(probeStart).Seconds()
		if cfg.churnCompactEachCycle && modeIsCachetag(cfg.mode) {
			barrierPrefix := fmt.Sprintf("driver_cycle_%d_live_generation_barrier", cycle)
			if profileUsesFellowDirectResidentZero(cfg) {
				if err := waitForPendingZero(client, baseURL, cfg); err != nil {
					return err
				}
				lines.add(barrierPrefix+"_skipped", 1)
				lines.add(barrierPrefix+"_skip_reason", "fellow_persistent_purgemap_resident_zero")
			} else {
				if err := waitForObjectCount(client, baseURL, cfg, cfg.objects, lines, barrierPrefix); err != nil {
					return err
				}
			}
			compactStart := beginPhase(lines, "rotating-tag-cycle-compact")
			compacted, err := compactRequest(client, baseURL)
			compactSeconds := time.Since(compactStart).Seconds()
			compactSecondsTotal += compactSeconds
			lines.add(fmt.Sprintf("driver_cycle_%d_compact_requested", cycle), 1)
			lines.add(fmt.Sprintf("driver_cycle_%d_compact_returned", cycle), compacted)
			lines.add(fmt.Sprintf("driver_cycle_%d_compact_wall_seconds", cycle), compactSeconds)
			if err != nil {
				return err
			}
		}
		completedCycles++
		time.Sleep(2 * time.Second)
	}
	if profileIsRotatingChurn(cfg.profile) && !cfg.churnCompactEachCycle && modeIsCachetag(cfg.mode) {
		if err := waitForPendingZero(client, baseURL, cfg); err != nil {
			return err
		}
		if profileIsDeterministicChurn(cfg.profile) {
			if err := waitForObjectCount(client, baseURL, cfg, 0, lines, "driver_rotating_tag_final_quiescence"); err != nil {
				return err
			}
		}
		compactStart := beginPhase(lines, "rotating-tag-compact")
		compacted, err := compactRequest(client, baseURL)
		compactSeconds := time.Since(compactStart).Seconds()
		compactSecondsTotal += compactSeconds
		lines.add("driver_rotating_tag_compact_requested", 1)
		lines.add("driver_rotating_tag_compact_returned", compacted)
		lines.add("driver_rotating_tag_compact_wall_seconds", compactSeconds)
		if err != nil {
			return err
		}
	}
	return nil
}

const phase6PartialDivisor = 8

func phase6CycleKind(cycle int) string {
	sequence := []string{
		"full-hard",
		"partial-hard",
		"threshold-churn",
		"soft-expiry",
		"ttl-expiry",
		"storage-pressure-lru",
		"full-hard",
		"threshold-churn",
		"partial-hard",
		"soft-expiry",
	}
	if cycle < len(sequence) {
		return sequence[cycle]
	}
	return sequence[cycle%len(sequence)]
}

func phase6FullKey(generation int) string {
	return fmt.Sprintf("phase6:full:%d", generation)
}

func phase6SoftKey(generation int) string {
	return fmt.Sprintf("phase6:soft:%d", generation)
}

func phase6PartialKey(generation int, part int) string {
	return fmt.Sprintf("phase6:partial:%d:%d", generation, part)
}

func phase6CycleMetric(lines *metrics, cycle int, suffix string, value any) {
	lines.add(fmt.Sprintf("driver_phase6_cycle_%d_%s", cycle, suffix), value)
}

func phase6PurgeGeneration(
	client *http.Client,
	baseURL string,
	cfg config,
	generation int,
	partial bool,
	lines *metrics,
	cycle int,
	label string,
) error {
	keys := []string{phase6FullKey(generation)}
	if partial {
		keys = keys[:0]
		for part := 0; part < phase6PartialDivisor-1; part++ {
			keys = append(keys, phase6PartialKey(generation, part))
		}
	}
	start := time.Now()
	published := 0
	for _, key := range keys {
		purged, err := purgeWithMode(client, baseURL, key, "hard", 0, false, true)
		if err != nil {
			return err
		}
		if purged == -1 {
			published++
		}
	}
	phase6CycleMetric(lines, cycle, label+"_purge_requests", len(keys))
	phase6CycleMetric(lines, cycle, label+"_purge_published", published)
	phase6CycleMetric(lines, cycle, label+"_purge_wall_seconds", time.Since(start).Seconds())
	if err := waitForPendingZero(client, baseURL, cfg); err != nil {
		return err
	}
	return nil
}

func phase6CompactAndWait(
	client *http.Client,
	baseURL string,
	cfg config,
	target int,
	lines *metrics,
	cycle int,
	label string,
) error {
	start := time.Now()
	compacted, err := compactRequest(client, baseURL)
	phase6CycleMetric(lines, cycle, label+"_compact_returned", compacted)
	phase6CycleMetric(lines, cycle, label+"_compact_wall_seconds", time.Since(start).Seconds())
	if err != nil {
		return err
	}
	if err := waitForObjectCount(client, baseURL, cfg, target, lines,
		fmt.Sprintf("driver_phase6_cycle_%d_%s_objects", cycle, label)); err != nil {
		return err
	}
	phase6CycleMetric(lines, cycle, label+"_target_objects", target)
	return nil
}

func phase6CycleSnapshot(cfg config, cycle int) error {
	if cfg.phaseMarkerDir == "" {
		return nil
	}
	phase := fmt.Sprintf("phase6-cycle-%02d", cycle)
	if err := writePhaseMarker(cfg, phase, "end"); err != nil {
		return err
	}
	return waitForPhaseSignal(cfg, phase, "snapshot",
		time.Duration(cfg.httpTimeout)*time.Second)
}

func phase6BanGeneration(client *http.Client, baseURL string, generation int) error {
	req, err := http.NewRequest("BAN", baseURL+"/__bench_ban", nil)
	if err != nil {
		return err
	}
	req.Header.Set("X-Bench-Phase6-Generation", strconv.Itoa(generation))
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return err
	}
	if resp.StatusCode != http.StatusOK || resp.Header.Get("X-Bench-Ban") != "accepted" {
		return fmt.Errorf("phase6 ban failed generation=%d status=%d body=%q", generation, resp.StatusCode, string(body))
	}
	return nil
}

func phase6WaitForBanDrain(cfg config, cycle int, stage string) error {
	phase := fmt.Sprintf("phase6-ban-%02d", cycle)
	if stage != "" {
		phase += "-" + stage
	}
	if err := writePhaseMarker(cfg, phase, "requested"); err != nil {
		return err
	}
	return waitForPhaseSignal(cfg, phase, "drained", time.Duration(cfg.httpTimeout)*time.Second)
}

func runNoindexPhase6BanDrain(client *http.Client, baseURL string, cfg config, lines *metrics) error {
	if !modeIsNoindex(cfg.mode) {
		return fmt.Errorf("phase6 no-index ban drain requires noindex mode, got %s", cfg.mode)
	}
	start := beginPhase(lines, "phase6-noindex-ban-drain")
	completed := 0
	defer func() {
		lines.add("driver_phase6_cycles", cfg.churnCycles)
		lines.add("driver_phase6_completed_cycles", completed)
		lines.add("driver_phase6_pressure_body_bytes", cfg.phase6PressureBody)
		lines.add("driver_phase6_quiet_seconds", cfg.phase6QuietSeconds)
		lines.add("driver_phase6_wall_seconds", time.Since(start).Seconds())
	}()

	for cycle := 0; cycle < cfg.churnCycles; cycle++ {
		cycleCfg := cfg
		cycleCfg.churnGeneration = cycle
		cycleCfg.phase6TTL = "long"
		cycleLatencies := newLatencyRecorder(200000)
		kind := "full-ban-drain"
		if cycle == 5 {
			kind = "storage-pressure-lru-ban-drain"
		}
		phase6CycleMetric(lines, cycle, "kind", kind)
		phase6CycleMetric(lines, cycle, "generation", cycle)
		loadStart := time.Now()
		loaded, err := loadObjectsDetailed(client, baseURL, cycleCfg, 0, cfg.objects, cycleLatencies)
		phase6CycleMetric(lines, cycle, "fill_requests", loaded.requests)
		phase6CycleMetric(lines, cycle, "fill_backend_objects", loaded.backendObjects)
		phase6CycleMetric(lines, cycle, "fill_wall_seconds", time.Since(loadStart).Seconds())
		if err != nil {
			return err
		}
		generations := []int{cycle}
		if cycle == 5 {
			baseBanStart := time.Now()
			if err := phase6BanGeneration(client, baseURL, cycle); err != nil {
				return err
			}
			phase6CycleMetric(lines, cycle, "base_ban_wall_seconds", time.Since(baseBanStart).Seconds())
			if err := phase6WaitForBanDrain(cfg, cycle, "base"); err != nil {
				return err
			}
			generations = generations[:0]
			pressureGeneration := cfg.churnCycles + cycle + 200
			for offset := 0; offset < 2; offset++ {
				pressureCfg := cycleCfg
				pressureCfg.churnGeneration = pressureGeneration + offset
				pressureCfg.phase6BodyBytes = cfg.phase6PressureBody
				pressureStart := time.Now()
				pressure, err := loadObjectsDetailed(client, baseURL, pressureCfg, 0, cfg.objects, cycleLatencies)
				phase6CycleMetric(lines, cycle, fmt.Sprintf("pressure_%d_backend_objects", offset), pressure.backendObjects)
				phase6CycleMetric(lines, cycle, fmt.Sprintf("pressure_%d_wall_seconds", offset), time.Since(pressureStart).Seconds())
				if err != nil {
					return err
				}
				generations = append(generations, pressureCfg.churnGeneration)
			}
		}
		banStart := time.Now()
		for _, generation := range generations {
			if err := phase6BanGeneration(client, baseURL, generation); err != nil {
				return err
			}
		}
		phase6CycleMetric(lines, cycle, "ban_requests", len(generations))
		phase6CycleMetric(lines, cycle, "ban_wall_seconds", time.Since(banStart).Seconds())
		if err := phase6WaitForBanDrain(cfg, cycle, ""); err != nil {
			return err
		}
		cycleLatencies.emit(fmt.Sprintf("driver_phase6_cycle_%d", cycle), lines)
		time.Sleep(time.Duration(cfg.phase6QuietSeconds) * time.Second)
		if err := phase6CycleSnapshot(cfg, cycle); err != nil {
			return err
		}
		completed++
	}
	return nil
}

func runPhase6FillDrain(client *http.Client, baseURL string, cfg config, lines *metrics) error {
	if !modeIsCachetag(cfg.mode) {
		return fmt.Errorf("phase6-fill-drain requires cachetag mode, got %s", cfg.mode)
	}
	start := beginPhase(lines, "phase6-fill-drain")
	completed := 0
	defer func() {
		lines.add("driver_phase6_cycles", cfg.churnCycles)
		lines.add("driver_phase6_completed_cycles", completed)
		lines.add("driver_phase6_partial_divisor", phase6PartialDivisor)
		lines.add("driver_phase6_pressure_body_bytes", cfg.phase6PressureBody)
		lines.add("driver_phase6_quiet_seconds", cfg.phase6QuietSeconds)
		lines.add("driver_phase6_wall_seconds", time.Since(start).Seconds())
	}()

	for cycle := 0; cycle < cfg.churnCycles; cycle++ {
		kind := phase6CycleKind(cycle)
		generation := cycle
		cycleCfg := cfg
		cycleCfg.churnGeneration = generation
		cycleCfg.phase6TTL = "long"
		cycleCfg.phase6BodyBytes = 0
		cycleLatencies := newLatencyRecorder(200000)
		phase6CycleMetric(lines, cycle, "kind", kind)
		phase6CycleMetric(lines, cycle, "generation", generation)

		loadStart := time.Now()
		loadResult, err := loadObjectsDetailed(client, baseURL, cycleCfg, 0, cfg.objects, cycleLatencies)
		phase6CycleMetric(lines, cycle, "fill_requests", loadResult.requests)
		phase6CycleMetric(lines, cycle, "fill_backend_objects", loadResult.backendObjects)
		phase6CycleMetric(lines, cycle, "fill_wall_seconds", time.Since(loadStart).Seconds())
		if err != nil {
			return err
		}
		if loadResult.backendObjects != int64(cfg.objects) {
			return fmt.Errorf("phase6 fill was not all-miss cycle=%d backend_objects=%d expected=%d", cycle, loadResult.backendObjects, cfg.objects)
		}
		if err := waitForPendingZero(client, baseURL, cfg); err != nil {
			return err
		}
		liveAfterFill, err := objectCountRequest(client, baseURL)
		if err != nil {
			return err
		}
		phase6CycleMetric(lines, cycle, "live_after_fill", liveAfterFill)

		targetPartial := cfg.objects / phase6PartialDivisor
		if targetPartial < 1 {
			targetPartial = 1
		}
		switch kind {
		case "full-hard":
			if err := phase6PurgeGeneration(client, baseURL, cfg, generation, false, lines, cycle, "full"); err != nil {
				return err
			}
			if err := phase6CompactAndWait(client, baseURL, cfg, 0, lines, cycle, "full"); err != nil {
				return err
			}
		case "partial-hard":
			if err := phase6PurgeGeneration(client, baseURL, cfg, generation, true, lines, cycle, "partial"); err != nil {
				return err
			}
			if err := phase6CompactAndWait(client, baseURL, cfg, targetPartial, lines, cycle, "partial"); err != nil {
				return err
			}
		case "threshold-churn":
			if err := phase6PurgeGeneration(client, baseURL, cfg, generation, true, lines, cycle, "threshold_partial"); err != nil {
				return err
			}
			if err := phase6CompactAndWait(client, baseURL, cfg, targetPartial, lines, cycle, "threshold_partial"); err != nil {
				return err
			}
			time.Sleep(time.Duration(cfg.phase6QuietSeconds) * time.Second)
			refillGeneration := cfg.churnCycles + cycle + 1
			refillCfg := cycleCfg
			refillCfg.churnGeneration = refillGeneration
			refillStart := time.Now()
			refill, err := loadObjectsDetailed(client, baseURL, refillCfg, 0, cfg.objects, cycleLatencies)
			phase6CycleMetric(lines, cycle, "threshold_refill_requests", refill.requests)
			phase6CycleMetric(lines, cycle, "threshold_refill_backend_objects", refill.backendObjects)
			phase6CycleMetric(lines, cycle, "threshold_refill_wall_seconds", time.Since(refillStart).Seconds())
			if err != nil {
				return err
			}
			if refill.backendObjects != int64(cfg.objects) {
				return fmt.Errorf("phase6 threshold refill was not all-miss cycle=%d backend_objects=%d expected=%d", cycle, refill.backendObjects, cfg.objects)
			}
			if err := phase6PurgeGeneration(client, baseURL, cfg, generation, false, lines, cycle, "threshold_old_cleanup"); err != nil {
				return err
			}
			if err := phase6PurgeGeneration(client, baseURL, cfg, refillGeneration, false, lines, cycle, "threshold_refill"); err != nil {
				return err
			}
			if err := phase6CompactAndWait(client, baseURL, cfg, 0, lines, cycle, "threshold_final"); err != nil {
				return err
			}
		case "soft-expiry":
			if _, err := purgeWithMode(client, baseURL, phase6SoftKey(generation), "soft", 0, false, true); err != nil {
				return err
			}
			phase6CycleMetric(lines, cycle, "soft_purge_published", 1)
			softCfg := cycleCfg
			softCfg.phase6TTL = "short"
			softCfg.residencyValidate = 0
			if err := probeCacheStates(client, baseURL, softCfg, 0, cfg.objects, lines,
				fmt.Sprintf("driver_phase6_cycle_%d_soft", cycle)); err != nil {
				return err
			}
			time.Sleep(2 * time.Second)
			if err := phase6CompactAndWait(client, baseURL, cfg, 0, lines, cycle, "soft"); err != nil {
				return err
			}
		case "ttl-expiry":
			ttlGeneration := cfg.churnCycles + cycle + 100
			ttlCfg := cycleCfg
			ttlCfg.churnGeneration = ttlGeneration
			ttlCfg.phase6TTL = "short"
			cohort := cfg.objects
			if cohort > 4096 {
				cohort = 4096
			}
			ttlStart := time.Now()
			ttlResult, err := loadObjectsDetailed(client, baseURL, ttlCfg, 0, cohort, cycleLatencies)
			phase6CycleMetric(lines, cycle, "ttl_cohort_requests", ttlResult.requests)
			phase6CycleMetric(lines, cycle, "ttl_cohort_backend_objects", ttlResult.backendObjects)
			phase6CycleMetric(lines, cycle, "ttl_cohort_wall_seconds", time.Since(ttlStart).Seconds())
			if err != nil {
				return err
			}
			if err := waitForPendingZero(client, baseURL, cfg); err != nil {
				return err
			}
			time.Sleep(2 * time.Second)
			if err := phase6CompactAndWait(client, baseURL, cfg, cfg.objects, lines, cycle, "ttl_cohort"); err != nil {
				return err
			}
			if err := phase6PurgeGeneration(client, baseURL, cfg, generation, false, lines, cycle, "ttl_full"); err != nil {
				return err
			}
			if err := phase6PurgeGeneration(client, baseURL, cfg, ttlGeneration, false, lines, cycle, "ttl_cohort_cleanup"); err != nil {
				return err
			}
			if err := phase6CompactAndWait(client, baseURL, cfg, 0, lines, cycle, "ttl_final"); err != nil {
				return err
			}
		case "storage-pressure-lru":
			if err := phase6PurgeGeneration(client, baseURL, cfg, generation, false, lines, cycle, "pressure_base_cleanup"); err != nil {
				return err
			}
			if err := phase6CompactAndWait(client, baseURL, cfg, 0, lines, cycle, "pressure_base_cleanup"); err != nil {
				return err
			}
			pressureCfg := cycleCfg
			pressureCfg.phase6BodyBytes = cfg.phase6PressureBody
			pressureGeneration := cfg.churnCycles + cycle + 200
			pressureCfg.churnGeneration = pressureGeneration
			pressureStart := time.Now()
			pressure, err := loadObjectsDetailed(client, baseURL, pressureCfg, 0, cfg.objects, cycleLatencies)
			phase6CycleMetric(lines, cycle, "pressure_backend_objects", pressure.backendObjects)
			phase6CycleMetric(lines, cycle, "pressure_wall_seconds", time.Since(pressureStart).Seconds())
			if err != nil {
				return err
			}
			pressureRefillGeneration := pressureGeneration + 1
			pressureCfg.churnGeneration = pressureRefillGeneration
			refillStart := time.Now()
			pressureRefill, err := loadObjectsDetailed(client, baseURL, pressureCfg, 0, cfg.objects, cycleLatencies)
			phase6CycleMetric(lines, cycle, "pressure_refill_backend_objects", pressureRefill.backendObjects)
			phase6CycleMetric(lines, cycle, "pressure_refill_wall_seconds", time.Since(refillStart).Seconds())
			if err != nil {
				return err
			}
			if err := waitForPendingZero(client, baseURL, cfg); err != nil {
				return err
			}
			pressureLive, err := objectCountRequest(client, baseURL)
			if err != nil {
				return err
			}
			phase6CycleMetric(lines, cycle, "live_after_pressure", pressureLive)
			if err := phase6PurgeGeneration(client, baseURL, cfg, generation, false, lines, cycle, "pressure_first_cleanup"); err != nil {
				return err
			}
			if err := phase6PurgeGeneration(client, baseURL, cfg, pressureRefillGeneration, false, lines, cycle, "pressure_refill_cleanup"); err != nil {
				return err
			}
			if err := phase6CompactAndWait(client, baseURL, cfg, 0, lines, cycle, "pressure_final"); err != nil {
				return err
			}
		default:
			return fmt.Errorf("unknown phase6 cycle kind %q", kind)
		}
		cycleLatencies.emit(fmt.Sprintf("driver_phase6_cycle_%d", cycle), lines)

		if kind == "partial-hard" {
			time.Sleep(time.Duration(cfg.phase6QuietSeconds) * time.Second)
			live, err := objectCountRequest(client, baseURL)
			if err != nil {
				return err
			}
			phase6CycleMetric(lines, cycle, "live_after_quiet", live)
		} else {
			time.Sleep(time.Duration(cfg.phase6QuietSeconds) * time.Second)
			if live, err := objectCountRequest(client, baseURL); err != nil {
				return err
			} else {
				phase6CycleMetric(lines, cycle, "live_after_quiet", live)
				if live != 0 {
					return fmt.Errorf("phase6 cycle did not return to zero cycle=%d kind=%s live=%d", cycle, kind, live)
				}
			}
		}
		if err := phase6CycleSnapshot(cfg, cycle); err != nil {
			return err
		}
		if kind == "partial-hard" {
			if err := phase6PurgeGeneration(client, baseURL, cfg, generation, false, lines, cycle, "partial_cleanup"); err != nil {
				return err
			}
			if err := phase6CompactAndWait(client, baseURL, cfg, 0, lines, cycle, "partial_cleanup"); err != nil {
				return err
			}
		}
		completed++
	}
	return nil
}

func runBulkPurge(client *http.Client, baseURL string, cfg config, lines *metrics) error {
	if err := runLoadObjectsPhase(client, baseURL, cfg, lines); err != nil {
		return err
	}
	beginPhase(lines, "load-residency")
	if err := validateResidentHits(client, baseURL, cfg, 0, cfg.objects, lines, "driver_load"); err != nil {
		return err
	}
	if err := runWarmHits(client, baseURL, cfg, lines); err != nil {
		return err
	}
	seen := map[string]bool{}
	uniqueKeys := make([]string, 0, cfg.buckets)
	totalExpected := 0
	totalActual := 0
	totalKeys := 0
	attemptedRequests := 0
	completedRequests := 0
	publishedRequests := 0
	if err := func() error {
		start := beginPhase(lines, "bulk-purge")
		defer func() {
			lines.add("driver_bulk_purge_requests", cfg.purgeRequests)
			lines.add("driver_bulk_purge_attempted_requests", attemptedRequests)
			lines.add("driver_bulk_purge_completed_requests", completedRequests)
			lines.add("driver_bulk_purge_published_requests", publishedRequests)
			lines.add("driver_bulk_purge_published", publishedRequests > 0)
			lines.add("driver_bulk_purge_keys", totalKeys)
			lines.add("driver_bulk_purge_unique_keys", len(uniqueKeys))
			lines.add("driver_bulk_purge_expected_deduplicated", true)
			lines.add("driver_bulk_purge_expected", totalExpected)
			lines.add("driver_bulk_purge_actual", totalActual)
			lines.add("driver_bulk_purge_actual_unknown", publishedRequests > 0)
			recordPhaseSeconds(lines, "bulk-purge", start)
		}()
		for request := 0; request < cfg.purgeRequests; request++ {
			keys := make([]string, 0, cfg.purgeKeysPerRequest)
			expected := 0
			for n := 0; n < cfg.purgeKeysPerRequest; n++ {
				key := fmt.Sprintf("bucket:%d", (request*cfg.purgeKeysPerRequest+n)%cfg.buckets)
				keys = append(keys, key)
				if !seen[key] {
					expected += expectedCount(cfg, key)
					seen[key] = true
					uniqueKeys = append(uniqueKeys, key)
				}
			}
			totalExpected += expected
			totalKeys += len(keys)
			attemptedRequests++
			purged, err := purge(client, baseURL, strings.Join(keys, " "), expected, false, modeIsCachetag(cfg.mode))
			if err != nil {
				return err
			}
			if purged == -1 {
				publishedRequests++
			} else {
				totalActual += purged
			}
			completedRequests++
		}
		return nil
	}(); err != nil {
		return err
	}
	if publishedRequests > 0 && cfg.purgeSettleDelay > 0 {
		lines.add("driver_bulk_purge_post_publication_ms", cfg.purgeSettleDelay)
		time.Sleep(time.Duration(cfg.purgeSettleDelay) * time.Millisecond)
	}
	beginPhase(lines, "bulk-purge-validation")
	return validatePurgedKeySetMisses(client, baseURL, cfg, uniqueKeys, lines, "driver_bulk_purge")
}

type operationGate struct {
	start time.Time
	rps   int64
	next  atomic.Int64
}

func newOperationGate(rps int) *operationGate {
	if rps <= 0 {
		return nil
	}
	return &operationGate{start: time.Now(), rps: int64(rps)}
}

func (g *operationGate) wait() {
	if g == nil {
		return
	}
	slot := g.next.Add(1) - 1
	target := g.start.Add(time.Duration(float64(slot) * float64(time.Second) / float64(g.rps)))
	if delay := time.Until(target); delay > 0 {
		time.Sleep(delay)
	}
}

type concurrentSecondStats struct {
	reads         []atomic.Int64
	inserts       []atomic.Int64
	purges        []atomic.Int64
	errors        []atomic.Int64
	readMaxNsec   []atomic.Int64
	insertMaxNsec []atomic.Int64
	purgeMaxNsec  []atomic.Int64
}

func newConcurrentSecondStats(seconds int) *concurrentSecondStats {
	if seconds < 1 {
		seconds = 1
	}
	return &concurrentSecondStats{
		reads:         make([]atomic.Int64, seconds),
		inserts:       make([]atomic.Int64, seconds),
		purges:        make([]atomic.Int64, seconds),
		errors:        make([]atomic.Int64, seconds),
		readMaxNsec:   make([]atomic.Int64, seconds),
		insertMaxNsec: make([]atomic.Int64, seconds),
		purgeMaxNsec:  make([]atomic.Int64, seconds),
	}
}

func concurrentSecond(start time.Time, seconds int) int {
	second := int(time.Since(start) / time.Second)
	if second < 0 {
		return 0
	}
	if second >= seconds {
		return seconds - 1
	}
	return second
}

func atomicMaxInt64(dst *atomic.Int64, value int64) {
	current := dst.Load()
	for value > current && !dst.CompareAndSwap(current, value) {
		current = dst.Load()
	}
}

func emitConcurrentSecondStats(prefix string, stats *concurrentSecondStats, lines *metrics) {
	seconds := len(stats.reads)
	readMin, readMax := int64(math.MaxInt64), int64(0)
	insertMin, insertMax := int64(math.MaxInt64), int64(0)
	purgeMin, purgeMax := int64(math.MaxInt64), int64(0)
	errorMax := int64(0)
	readLatencyMax, insertLatencyMax, purgeLatencyMax := int64(0), int64(0), int64(0)
	for second := 0; second < seconds; second++ {
		reads := stats.reads[second].Load()
		inserts := stats.inserts[second].Load()
		purges := stats.purges[second].Load()
		errors := stats.errors[second].Load()
		readLatency := stats.readMaxNsec[second].Load()
		insertLatency := stats.insertMaxNsec[second].Load()
		purgeLatency := stats.purgeMaxNsec[second].Load()
		lines.add(fmt.Sprintf("%s_second_%03d_reads", prefix, second), reads)
		lines.add(fmt.Sprintf("%s_second_%03d_inserts", prefix, second), inserts)
		lines.add(fmt.Sprintf("%s_second_%03d_purges", prefix, second), purges)
		lines.add(fmt.Sprintf("%s_second_%03d_errors", prefix, second), errors)
		lines.add(fmt.Sprintf("%s_second_%03d_read_latency_max_seconds", prefix, second), float64(readLatency)/float64(time.Second))
		lines.add(fmt.Sprintf("%s_second_%03d_insert_latency_max_seconds", prefix, second), float64(insertLatency)/float64(time.Second))
		lines.add(fmt.Sprintf("%s_second_%03d_purge_latency_max_seconds", prefix, second), float64(purgeLatency)/float64(time.Second))
		if reads < readMin {
			readMin = reads
		}
		if reads > readMax {
			readMax = reads
		}
		if inserts < insertMin {
			insertMin = inserts
		}
		if inserts > insertMax {
			insertMax = inserts
		}
		if purges < purgeMin {
			purgeMin = purges
		}
		if purges > purgeMax {
			purgeMax = purges
		}
		if errors > errorMax {
			errorMax = errors
		}
		if readLatency > readLatencyMax {
			readLatencyMax = readLatency
		}
		if insertLatency > insertLatencyMax {
			insertLatencyMax = insertLatency
		}
		if purgeLatency > purgeLatencyMax {
			purgeLatencyMax = purgeLatency
		}
	}
	if readMin == math.MaxInt64 {
		readMin = 0
	}
	if insertMin == math.MaxInt64 {
		insertMin = 0
	}
	if purgeMin == math.MaxInt64 {
		purgeMin = 0
	}
	lines.add(prefix+"_seconds", seconds)
	lines.add(prefix+"_read_rps_1s_min", readMin)
	lines.add(prefix+"_read_rps_1s_max", readMax)
	lines.add(prefix+"_insert_rps_1s_min", insertMin)
	lines.add(prefix+"_insert_rps_1s_max", insertMax)
	lines.add(prefix+"_purge_rps_1s_min", purgeMin)
	lines.add(prefix+"_purge_rps_1s_max", purgeMax)
	lines.add(prefix+"_errors_1s_max", errorMax)
	lines.add(prefix+"_read_latency_1s_max_seconds", float64(readLatencyMax)/float64(time.Second))
	lines.add(prefix+"_insert_latency_1s_max_seconds", float64(insertLatencyMax)/float64(time.Second))
	lines.add(prefix+"_purge_latency_1s_max_seconds", float64(purgeLatencyMax)/float64(time.Second))
}

type phase4ReadWindow struct {
	scheduled                  int64
	started                    int64
	completed                  int64
	skippedSlots               int64
	lateStarts                 int64
	requests                   int64
	hits                       int64
	misses                     int64
	staleResponses             int64
	staleHits                  int64
	olderResponses             int64
	newerResponses             int64
	currentEpochStaleResponses int64
	errors                     int64
	wallSeconds                float64
	recorder                   *latencyRecorder
	err                        error
	evidenceMu                 sync.Mutex
	evidence                   map[string]int64
}

const phase4SampleSchema = "phase4-request-v1"

type phase4RequestSample struct {
	sequence                uint64
	object                  int
	phaseHint               string
	scheduledStartNS        int64
	requestStartNS          int64
	requestEndNS            int64
	durationNS              int64
	schedulingLagNS         int64
	skippedSlotsBefore      int64
	cacheState              string
	requestedEpoch          uint64
	returnedEpoch           uint64
	errorClass              string
	beganAfterEpochBoundary bool
}

type phase4RequestRecorder struct {
	mu      sync.Mutex
	samples []phase4RequestSample
	limit   int
	dropped atomic.Int64
	next    atomic.Uint64
}

func newPhase4RequestRecorder(limit int) *phase4RequestRecorder {
	return &phase4RequestRecorder{samples: make([]phase4RequestSample, 0, limit), limit: limit}
}

func (r *phase4RequestRecorder) nextSequence() uint64 {
	return r.next.Add(1)
}

func (r *phase4RequestRecorder) add(sample phase4RequestSample) {
	r.mu.Lock()
	if len(r.samples) < r.limit {
		r.samples = append(r.samples, sample)
	} else {
		r.dropped.Add(1)
	}
	r.mu.Unlock()
}

func (r *phase4RequestRecorder) snapshot() []phase4RequestSample {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]phase4RequestSample(nil), r.samples...)
}

type phase4Pacer struct {
	mu       sync.Mutex
	start    time.Time
	rps      int64
	nextSlot int64
}

func newPhase4Pacer(rps int, start time.Time) *phase4Pacer {
	return &phase4Pacer{start: start, rps: int64(rps)}
}

func (p *phase4Pacer) wait() (int64, int64, int64) {
	if p.rps <= 0 {
		return -1, 0, 0
	}
	interval := time.Duration(float64(time.Second) / float64(p.rps))
	p.mu.Lock()
	now := time.Now()
	target := p.start.Add(time.Duration(float64(p.nextSlot) * float64(time.Second) / float64(p.rps)))
	skipped := int64(0)
	if now.After(target.Add(interval)) {
		skipped = int64(now.Sub(target) / interval)
		p.nextSlot += skipped
		target = p.start.Add(time.Duration(float64(p.nextSlot) * float64(time.Second) / float64(p.rps)))
	}
	p.nextSlot++
	p.mu.Unlock()
	if delay := time.Until(target); delay > 0 {
		time.Sleep(delay)
	}
	actual := time.Now()
	lag := actual.Sub(target)
	if lag < 0 {
		lag = 0
	}
	return target.Sub(p.start).Nanoseconds(), lag.Nanoseconds(), skipped
}

type phase4Boundaries struct {
	origin                time.Time
	epochBoundaryNS       atomic.Int64
	readerWindowStartNS   int64
	readerWindowEndNS     int64
	purgeRequestStartNS   int64
	purgeResponseEndNS    int64
	sealPurgeStartNS      int64
	sealPurgeEndNS        int64
	compactRequestStartNS int64
	compactResponseEndNS  int64
	preStartNS            int64
	preEndNS              int64
	postStartNS           int64
	postEndNS             int64
	guardNS               int64
	compactPresent        bool
}

func newPhase4Boundaries(origin time.Time, guard time.Duration) *phase4Boundaries {
	b := &phase4Boundaries{
		origin: origin, readerWindowStartNS: -1, readerWindowEndNS: -1,
		purgeRequestStartNS: -1, purgeResponseEndNS: -1,
		sealPurgeStartNS: -1, sealPurgeEndNS: -1,
		compactRequestStartNS: -1, compactResponseEndNS: -1,
		preStartNS: -1, preEndNS: -1, postStartNS: -1, postEndNS: -1,
		guardNS: guard.Nanoseconds(),
	}
	b.epochBoundaryNS.Store(-1)
	return b
}

func phase4ArtifactPath(metricsPath string, suffix string) string {
	if metricsPath == "" {
		return ""
	}
	return strings.TrimSuffix(metricsPath, ".driver") + "." + suffix
}

func phase4TSV(value string) string {
	return strings.NewReplacer("\t", " ", "\r", " ", "\n", " ").Replace(value)
}

func writePhase4RequestSamples(path string, samples []phase4RequestSample) error {
	if path == "" {
		return nil
	}
	var b strings.Builder
	b.WriteString("schema\tsequence\tobject\tphase_hint\tscheduled_start_ns\trequest_start_ns\trequest_end_ns\tduration_ns\tscheduling_lag_ns\tskipped_slots_before\tcache_state\trequested_epoch\treturned_epoch\terror_class\tbegan_after_epoch_boundary\n")
	for _, s := range samples {
		fmt.Fprintf(&b, "%s\t%d\t%d\t%s\t%d\t%d\t%d\t%d\t%d\t%d\t%s\t%d\t%d\t%s\t%d\n",
			phase4SampleSchema, s.sequence, s.object, phase4TSV(s.phaseHint),
			s.scheduledStartNS, s.requestStartNS, s.requestEndNS, s.durationNS,
			s.schedulingLagNS, s.skippedSlotsBefore, phase4TSV(s.cacheState), s.requestedEpoch,
			s.returnedEpoch, phase4TSV(s.errorClass), boolInt(s.beganAfterEpochBoundary))
	}
	return os.WriteFile(path, []byte(b.String()), 0644)
}

func writePhase4Boundaries(path string, b *phase4Boundaries) error {
	if path == "" {
		return nil
	}
	values := []struct {
		key   string
		value any
	}{
		{"schema", "phase4-boundaries-v1"},
		{"reader_window_start_ns", b.readerWindowStartNS},
		{"purge_request_start_ns", b.purgeRequestStartNS},
		{"purge_response_end_ns", b.purgeResponseEndNS},
		{"accepted_epoch_transition_ns", b.epochBoundaryNS.Load()},
		{"seal_purge_start_ns", b.sealPurgeStartNS},
		{"seal_purge_end_ns", b.sealPurgeEndNS},
		{"compact_present", boolInt(b.compactPresent)},
		{"compact_request_start_ns", b.compactRequestStartNS},
		{"compact_response_end_ns", b.compactResponseEndNS},
		{"reader_window_end_ns", b.readerWindowEndNS},
		{"pre_start_ns", b.preStartNS}, {"pre_end_ns", b.preEndNS},
		{"post_start_ns", b.postStartNS}, {"post_end_ns", b.postEndNS},
		{"attribution_guard_ns", b.guardNS},
	}
	var out strings.Builder
	out.WriteString("key\tvalue\n")
	for _, item := range values {
		fmt.Fprintf(&out, "%s\t%v\n", item.key, item.value)
	}
	return os.WriteFile(path, []byte(out.String()), 0644)
}

func (r *phase4ReadWindow) recordEpochEvidence(response objectResponse) {
	key := fmt.Sprintf("requested_%d_returned_%d_cache_%s", response.requestedEpoch, response.originGeneration, response.cacheState)
	r.evidenceMu.Lock()
	r.evidence[key]++
	r.evidenceMu.Unlock()
}

func latencySamplePath(metricsPath string, suffix string) string {
	if metricsPath == "" {
		return ""
	}
	base := strings.TrimSuffix(metricsPath, ".driver")
	return base + "." + suffix + ".latency_samples.tsv"
}

func writeLatencySamples(path string, samples []float64) error {
	if path == "" {
		return nil
	}
	var b strings.Builder
	b.WriteString("seconds\n")
	for _, sample := range samples {
		b.WriteString(fmt.Sprintf("%.9f\n", sample))
	}
	return os.WriteFile(path, []byte(b.String()), 0644)
}

func writeIndexedLatencySamples(path string, samples []float64) error {
	if path == "" {
		return nil
	}
	var b strings.Builder
	b.WriteString("request_index\tseconds\n")
	for index, sample := range samples {
		fmt.Fprintf(&b, "%d\t%.9f\n", index+1, sample)
	}
	return os.WriteFile(path, []byte(b.String()), 0644)
}

func startPhase4Readers(
	client *http.Client,
	baseURL string,
	cfg config,
	validateFresh *atomic.Bool,
	stop <-chan struct{},
	phaseHint string,
	origin time.Time,
	epochBoundary *atomic.Int64,
	rich *phase4RequestRecorder,
) (*phase4ReadWindow, func()) {
	result := &phase4ReadWindow{recorder: newLatencyRecorder(500000), evidence: make(map[string]int64)}
	var firstErr error
	var firstErrMu sync.Mutex
	var next atomic.Int64
	start := time.Now()
	pacer := newPhase4Pacer(cfg.concurrentTargetRPS, start)
	var wg sync.WaitGroup
	recordErr := func(err error) {
		if err == nil {
			return
		}
		atomic.AddInt64(&result.errors, 1)
		firstErrMu.Lock()
		if firstErr == nil {
			firstErr = err
		}
		firstErrMu.Unlock()
	}
	for worker := 0; worker < cfg.concurrentReaders; worker++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				select {
				case <-stop:
					return
				default:
				}
				scheduledNS, schedulingLagNS, skipped := pacer.wait()
				if scheduledNS >= 0 {
					scheduledNS += start.Sub(origin).Nanoseconds()
				}
				atomic.AddInt64(&result.scheduled, skipped+1)
				atomic.AddInt64(&result.skippedSlots, skipped)
				if schedulingLagNS > int64(time.Millisecond) {
					atomic.AddInt64(&result.lateStarts, 1)
				}
				select {
				case <-stop:
					return
				default:
				}
				obj := int(next.Add(1)-1) % cfg.objects
				requestedEpoch := uint64(1)
				if cfg.originEpoch != nil {
					requestedEpoch = cfg.originEpoch.beginRequest()
				}
				t0 := time.Now()
				startNS := t0.Sub(origin).Nanoseconds()
				atomic.AddInt64(&result.started, 1)
				resp, err := objectRequestAtEpoch(client, baseURL, cfg, obj, requestedEpoch)
				t1 := time.Now()
				if cfg.originEpoch != nil {
					cfg.originEpoch.endRequest()
				}
				elapsed := t1.Sub(t0)
				atomic.AddInt64(&result.completed, 1)
				result.recorder.add(elapsed)
				boundaryNS := epochBoundary.Load()
				sample := phase4RequestSample{
					sequence: rich.nextSequence(), object: obj, phaseHint: phaseHint,
					scheduledStartNS: scheduledNS, requestStartNS: startNS,
					requestEndNS: t1.Sub(origin).Nanoseconds(), durationNS: elapsed.Nanoseconds(),
					schedulingLagNS: schedulingLagNS, requestedEpoch: requestedEpoch,
					skippedSlotsBefore:      skipped,
					beganAfterEpochBoundary: boundaryNS >= 0 && startNS >= boundaryNS,
				}
				if err != nil {
					sample.errorClass = "request_error"
					rich.add(sample)
					recordErr(err)
					continue
				}
				sample.cacheState = resp.cacheState
				sample.returnedEpoch = resp.originGeneration
				sample.errorClass = "ok"
				rich.add(sample)
				atomic.AddInt64(&result.requests, 1)
				if resp.cacheState == "hit" {
					atomic.AddInt64(&result.hits, 1)
				} else {
					atomic.AddInt64(&result.misses, 1)
				}
				result.recordEpochEvidence(resp)
				if validateFresh != nil && validateFresh.Load() && isStaleGeneration(resp) {
					atomic.AddInt64(&result.staleResponses, 1)
					switch generationClassification(resp) {
					case "older":
						atomic.AddInt64(&result.olderResponses, 1)
					case "newer":
						atomic.AddInt64(&result.newerResponses, 1)
					}
					if resp.cacheState == "hit" {
						atomic.AddInt64(&result.staleHits, 1)
					}
					if cfg.originEpoch != nil && resp.requestedEpoch == cfg.originEpoch.current() {
						atomic.AddInt64(&result.currentEpochStaleResponses, 1)
					}
				}
			}
		}()
	}
	return result, func() {
		wg.Wait()
		result.wallSeconds = time.Since(start).Seconds()
		firstErrMu.Lock()
		result.err = firstErr
		firstErrMu.Unlock()
	}
}

func emitPhase4ReadWindow(cfg config, prefix string, result *phase4ReadWindow, lines *metrics) error {
	lines.add(prefix+"_readers", cfg.concurrentReaders)
	lines.add(prefix+"_target_rps", cfg.concurrentTargetRPS)
	lines.add(prefix+"_wall_seconds", result.wallSeconds)
	lines.add(prefix+"_scheduled", result.scheduled)
	lines.add(prefix+"_started", result.started)
	lines.add(prefix+"_completed", result.completed)
	lines.add(prefix+"_skipped_pacing_slots", result.skippedSlots)
	lines.add(prefix+"_late_starts", result.lateStarts)
	lines.add(prefix+"_requests", result.requests)
	if result.wallSeconds > 0 {
		lines.add(prefix+"_requests_per_second", float64(result.requests)/result.wallSeconds)
	}
	lines.add(prefix+"_hits", result.hits)
	lines.add(prefix+"_misses", result.misses)
	lines.add(prefix+"_stale_responses", result.staleResponses)
	lines.add(prefix+"_stale_hits", result.staleHits)
	lines.add(prefix+"_stale_older_responses", result.olderResponses)
	lines.add(prefix+"_stale_newer_responses", result.newerResponses)
	lines.add(prefix+"_current_epoch_stale_responses", result.currentEpochStaleResponses)
	lines.add(prefix+"_errors", result.errors)
	result.evidenceMu.Lock()
	for key, count := range result.evidence {
		lines.add(prefix+"_epoch_evidence_"+key, count)
	}
	result.evidenceMu.Unlock()
	result.recorder.emit(prefix, lines)
	path := latencySamplePath(cfg.metricsPath, prefix)
	lines.add(prefix+"_latency_samples_path", path)
	if err := writeLatencySamples(path, result.recorder.snapshot()); err != nil {
		return err
	}
	return result.err
}

func validatePhase4CurrentEpoch(prefix string, result *phase4ReadWindow) error {
	if result.currentEpochStaleResponses == 0 {
		return nil
	}
	return fmt.Errorf("%s observed %d stale responses for requests using the current epoch", prefix, result.currentEpochStaleResponses)
}

func runPhase4TimedReadWindow(
	client *http.Client,
	baseURL string,
	cfg config,
	lines *metrics,
	prefix string,
	seconds int,
	validateFresh bool,
	origin time.Time,
	boundaries *phase4Boundaries,
	rich *phase4RequestRecorder,
) error {
	phase := strings.TrimPrefix(prefix, "driver_phase4_")
	start := beginPhase(lines, "phase4-"+phase)
	if err := writePhaseMarker(cfg, "phase4-"+phase, "start"); err != nil {
		return err
	}
	stop := make(chan struct{})
	var fresh atomic.Bool
	fresh.Store(validateFresh)
	if phase == "pre" {
		boundaries.preStartNS = time.Since(origin).Nanoseconds()
	} else if phase == "post" {
		boundaries.postStartNS = time.Since(origin).Nanoseconds()
	}
	result, wait := startPhase4Readers(client, baseURL, cfg, &fresh, stop,
		phase, origin, &boundaries.epochBoundaryNS, rich)
	time.Sleep(time.Duration(seconds) * time.Second)
	close(stop)
	wait()
	if phase == "pre" {
		boundaries.preEndNS = time.Since(origin).Nanoseconds()
	} else if phase == "post" {
		boundaries.postEndNS = time.Since(origin).Nanoseconds()
	}
	markerErr := writePhaseMarker(cfg, "phase4-"+phase, "end")
	recordPhaseSeconds(lines, "phase4-"+phase, start)
	if err := emitPhase4ReadWindow(cfg, prefix, result, lines); err != nil {
		return err
	}
	if validateFresh {
		if err := validatePhase4CurrentEpoch(prefix, result); err != nil {
			return err
		}
	}
	return markerErr
}

func runPhase4SweepWindow(client *http.Client, baseURL string, cfg config, lines *metrics,
	origin time.Time, boundaries *phase4Boundaries, rich *phase4RequestRecorder,
	refillControl bool) error {
	start := beginPhase(lines, "phase4-sweep")
	if err := writePhaseMarker(cfg, "phase4-sweep", "start"); err != nil {
		return err
	}
	stop := make(chan struct{})
	var validateFresh atomic.Bool
	validateFresh.Store(true)
	boundaries.readerWindowStartNS = time.Since(origin).Nanoseconds()
	result, wait := startPhase4Readers(client, baseURL, cfg, &validateFresh, stop,
		"measurement", origin, &boundaries.epochBoundaryNS, rich)
	time.Sleep(500 * time.Millisecond)
	expected := expectedCount(cfg, cfg.purgeKey)
	purgeStarted := time.Now()
	boundaries.purgeRequestStartNS = purgeStarted.Sub(origin).Nanoseconds()
	purged, err := purge(client, baseURL, cfg.purgeKey, expected, false, true)
	purgeEnded := time.Now()
	boundaries.purgeResponseEndNS = purgeEnded.Sub(origin).Nanoseconds()
	purgeSeconds := purgeEnded.Sub(purgeStarted).Seconds()
	sealed := 0
	sealSeconds := 0.0
	var sealErr error
	if err == nil {
		acceptedEpoch := acceptedPurgeEpoch(cfg, purged)
		lines.add("driver_phase4_sweep_origin_epoch", acceptedEpoch)
		if purged == -1 || purged > 0 {
			boundaries.epochBoundaryNS.Store(time.Since(origin).Nanoseconds())
			sealStarted := time.Now()
			boundaries.sealPurgeStartNS = sealStarted.Sub(origin).Nanoseconds()
			sealed, sealErr = purge(client, baseURL, cfg.purgeKey, expected, false, true)
			sealEnded := time.Now()
			boundaries.sealPurgeEndNS = sealEnded.Sub(origin).Nanoseconds()
			sealSeconds = sealEnded.Sub(sealStarted).Seconds()
		}
	}
	compacted := 0
	compactSeconds := 0.0
	var compactErr error
	if !refillControl {
		boundaries.compactPresent = true
		compactStarted := time.Now()
		boundaries.compactRequestStartNS = compactStarted.Sub(origin).Nanoseconds()
		compacted, compactErr = compactRequest(client, baseURL)
		compactEnded := time.Now()
		boundaries.compactResponseEndNS = compactEnded.Sub(origin).Nanoseconds()
		compactSeconds = compactEnded.Sub(compactStarted).Seconds()
	}
	if markerErr := writePhaseMarker(cfg, "phase4-compact", "end"); markerErr != nil && compactErr == nil {
		compactErr = markerErr
	}
	lines.add("driver_phase4_sweep_purge_key", cfg.purgeKey)
	lines.add("driver_phase4_sweep_purge_expected", expected)
	lines.add("driver_phase4_sweep_purge_actual", purged)
	lines.add("driver_phase4_sweep_purge_published", purged == -1)
	lines.add("driver_phase4_sweep_purge_wall_seconds", purgeSeconds)
	lines.add("driver_phase4_sweep_seal_purge_actual", sealed)
	lines.add("driver_phase4_sweep_seal_purge_wall_seconds", sealSeconds)
	lines.add("driver_phase4_sweep_compact_returned", compacted)
	lines.add("driver_phase4_sweep_compact_wall_seconds", compactSeconds)
	lines.add("driver_phase4_refill_control", boolInt(refillControl))
	deadline := start.Add(time.Duration(cfg.phase4SweepSeconds) * time.Second)
	if sleep := time.Until(deadline); sleep > 0 {
		time.Sleep(sleep)
	}
	close(stop)
	wait()
	boundaries.readerWindowEndNS = time.Since(origin).Nanoseconds()
	markerErr := writePhaseMarker(cfg, "phase4-sweep", "end")
	recordPhaseSeconds(lines, "phase4-sweep", start)
	if emitErr := emitPhase4ReadWindow(cfg, "driver_phase4_sweep", result, lines); emitErr != nil && err == nil {
		err = emitErr
	}
	if err != nil {
		return err
	}
	if sealErr != nil {
		return sealErr
	}
	if sealed != -1 && sealed != expected {
		return fmt.Errorf("phase4 seal purge key=%q returned %d, expected accepted publication -1 or exact %d", cfg.purgeKey, sealed, expected)
	}
	if compactErr != nil {
		return compactErr
	}
	if purged != -1 && purged != expected {
		return fmt.Errorf("phase4 purge key=%q returned %d, expected accepted publication -1 or exact %d", cfg.purgeKey, purged, expected)
	}
	if err := validatePhase4CurrentEpoch("driver_phase4_sweep", result); err != nil {
		return err
	}
	return markerErr
}

func runPhase4SweepLatency(client *http.Client, baseURL string, cfg config, lines *metrics) (runErr error) {
	if !modeIsCachetag(cfg.mode) {
		return fmt.Errorf("phase4-sweep-latency requires cachetag mode, got %s", cfg.mode)
	}
	refillControl := cfg.profile == "phase4-refill-control"
	origin := time.Now()
	boundaries := newPhase4Boundaries(origin, time.Duration(cfg.phase4GuardMS)*time.Millisecond)
	limit := 1000000
	if cfg.concurrentTargetRPS > 0 {
		seconds := cfg.phase4PreSeconds + cfg.phase4SweepSeconds + cfg.phase4PostSeconds
		limit = cfg.concurrentTargetRPS*seconds*6/5 + cfg.concurrentReaders*8
	}
	rich := newPhase4RequestRecorder(limit)
	samplesPath := phase4ArtifactPath(cfg.metricsPath, "phase4_requests.tsv")
	boundariesPath := phase4ArtifactPath(cfg.metricsPath, "phase4_boundaries.tsv")
	defer func() {
		lines.add("driver_phase4_sample_schema", phase4SampleSchema)
		lines.add("driver_phase4_samples_path", samplesPath)
		lines.add("driver_phase4_boundaries_path", boundariesPath)
		lines.add("driver_phase4_samples", len(rich.snapshot()))
		lines.add("driver_phase4_dropped_samples", rich.dropped.Load())
		lines.add("driver_phase4_attribution_guard_ms", cfg.phase4GuardMS)
		lines.add("driver_phase4_refill_control", boolInt(refillControl))
		if err := writePhase4RequestSamples(samplesPath, rich.snapshot()); err != nil && runErr == nil {
			runErr = err
		}
		if err := writePhase4Boundaries(boundariesPath, boundaries); err != nil && runErr == nil {
			runErr = err
		}
		if rich.dropped.Load() != 0 && runErr == nil {
			runErr = fmt.Errorf("phase4 dropped %d rich request samples", rich.dropped.Load())
		}
	}()
	if err := runLoadObjectsPhase(client, baseURL, cfg, lines); err != nil {
		return err
	}
	beginPhase(lines, "load-residency")
	if err := validateResidentHits(client, baseURL, cfg, 0, cfg.objects, lines, "driver_load"); err != nil {
		return err
	}
	if err := runWarmHits(client, baseURL, cfg, lines); err != nil {
		return err
	}
	lines.add("driver_phase4_pre_seconds_configured", cfg.phase4PreSeconds)
	lines.add("driver_phase4_sweep_seconds_configured", cfg.phase4SweepSeconds)
	lines.add("driver_phase4_post_seconds_configured", cfg.phase4PostSeconds)
	lines.add("driver_phase4_attribution_guard_ms_configured", cfg.phase4GuardMS)
	if err := runPhase4TimedReadWindow(client, baseURL, cfg, lines, "driver_phase4_pre", cfg.phase4PreSeconds, false,
		origin, boundaries, rich); err != nil {
		return err
	}
	if err := runPhase4SweepWindow(client, baseURL, cfg, lines, origin, boundaries, rich, refillControl); err != nil {
		return err
	}
	if err := runPhase4TimedReadWindow(client, baseURL, cfg, lines, "driver_phase4_post", cfg.phase4PostSeconds, true,
		origin, boundaries, rich); err != nil {
		return err
	}
	if err := waitForPendingZero(client, baseURL, cfg); err != nil {
		return err
	}
	if refillControl {
		return nil
	}
	return validatePurgeWindow(client, baseURL, cfg, cfg.purgeKey, lines, "driver_phase4", false)
}

func phaseSignalPath(cfg config, phase string, event string) string {
	prefix := cfg.phaseMarkerPrefix
	if prefix == "" {
		prefix = phaseName(cfg.mode)
	}
	return filepath.Join(cfg.phaseMarkerDir, fmt.Sprintf("%s.%s.%s", prefix, phaseName(phase), event))
}

func waitForPhaseSignal(cfg config, phase string, event string, timeout time.Duration) error {
	if cfg.phaseMarkerDir == "" {
		return fmt.Errorf("phase marker directory is required for %s.%s", phase, event)
	}
	path := phaseSignalPath(cfg, phase, event)
	deadline := time.Now().Add(timeout)
	for {
		if _, err := os.Stat(path); err == nil {
			return nil
		} else if !os.IsNotExist(err) {
			return err
		}
		if !time.Now().Before(deadline) {
			return fmt.Errorf("timed out waiting for phase signal %s", path)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func heldPublicationRequest(client *http.Client, baseURL string) error {
	req, err := http.NewRequest(http.MethodGet, baseURL+"/__bench_phase5_hold", nil)
	if err != nil {
		return err
	}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return err
	}
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("held publication request failed status=%d body=%q", resp.StatusCode, string(body))
	}
	return nil
}

func runPhase5HeldTraffic(client *http.Client, baseURL string, cfg config, lines *metrics) error {
	start := beginPhase(lines, "phase5-held-load")
	if err := writePhaseMarker(cfg, "phase5-held-load", "start"); err != nil {
		return err
	}
	seconds := (cfg.phase5HoldMS + 999) / 1000
	if seconds < 1 {
		seconds = 1
	}
	deadline := start.Add(time.Duration(cfg.phase5HoldMS) * time.Millisecond)
	stats := newConcurrentSecondStats(seconds)
	readLatencies := newLatencyRecorder(200000)
	purgeLatencies := newLatencyRecorder(200000)
	var reads, purges, purgesQueued, errors int64
	var nextRead atomic.Int64
	var nextPurge atomic.Int64
	var firstErr error
	var firstErrMu sync.Mutex
	gate := newOperationGate(cfg.concurrentTargetRPS)
	recordErr := func(err error) {
		if err == nil {
			return
		}
		atomic.AddInt64(&errors, 1)
		firstErrMu.Lock()
		if firstErr == nil {
			firstErr = err
		}
		firstErrMu.Unlock()
	}
	var wg sync.WaitGroup
	for worker := 0; worker < cfg.concurrentReaders; worker++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for time.Now().Before(deadline) {
				gate.wait()
				obj := int(nextRead.Add(1)-1) % cfg.objects
				t0 := time.Now()
				_, err := objectRequest(client, baseURL, cfg, obj)
				elapsed := time.Since(t0)
				second := concurrentSecond(start, seconds)
				atomicMaxInt64(&stats.readMaxNsec[second], elapsed.Nanoseconds())
				readLatencies.add(elapsed)
				if err != nil {
					stats.errors[second].Add(1)
					recordErr(err)
				} else {
					atomic.AddInt64(&reads, 1)
					stats.reads[second].Add(1)
				}
			}
		}()
	}
	for worker := 0; worker < cfg.concurrentPurgers; worker++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			interval := time.Duration(cfg.concurrentPurgers) * time.Second / time.Duration(cfg.concurrentPurgeRate)
			if interval < time.Millisecond {
				interval = time.Millisecond
			}
			ticker := time.NewTicker(interval)
			defer ticker.Stop()
			for time.Now().Before(deadline) {
				<-ticker.C
				purgeIndex := nextPurge.Add(1)
				key := fmt.Sprintf("phase5:purge:%06d", purgeIndex)
				if cfg.phase5CapPurges > 0 {
					key = fmt.Sprintf("phase5:cap:%06d", purgeIndex%int64(cfg.phase5CapPurges))
				}
				t0 := time.Now()
				purged, err := purge(client, baseURL, key, 0, false, true)
				elapsed := time.Since(t0)
				second := concurrentSecond(start, seconds)
				atomicMaxInt64(&stats.purgeMaxNsec[second], elapsed.Nanoseconds())
				purgeLatencies.add(elapsed)
				if err != nil {
					stats.errors[second].Add(1)
					recordErr(err)
				} else {
					atomic.AddInt64(&purges, 1)
					stats.purges[second].Add(1)
					if purged == -1 {
						atomic.AddInt64(&purgesQueued, 1)
					}
				}
			}
		}()
	}
	wg.Wait()
	markerErr := writePhaseMarker(cfg, "phase5-held-load", "end")
	firstErrMu.Lock()
	err := firstErr
	firstErrMu.Unlock()
	lines.add("driver_phase5_held_load_ms_configured", cfg.phase5HoldMS)
	lines.add("driver_phase5_held_cap_purges_configured", cfg.phase5CapPurges)
	lines.add("driver_phase5_held_readers", cfg.concurrentReaders)
	lines.add("driver_phase5_held_purgers", cfg.concurrentPurgers)
	lines.add("driver_phase5_held_target_rps", cfg.concurrentTargetRPS)
	lines.add("driver_phase5_held_purge_rate", cfg.concurrentPurgeRate)
	lines.add("driver_phase5_held_reads", reads)
	lines.add("driver_phase5_held_purges", purges)
	lines.add("driver_phase5_held_purges_published", purgesQueued)
	lines.add("driver_phase5_held_errors", errors)
	readLatencies.emit("driver_phase5_held_read", lines)
	purgeLatencies.emit("driver_phase5_held_purge", lines)
	emitConcurrentSecondStats("driver_phase5_held", stats, lines)
	recordPhaseSeconds(lines, "phase5-held-load", start)
	if err != nil {
		return err
	}
	return markerErr
}

func runPhase5HeldPublication(client *http.Client, baseURL string, cfg config, lines *metrics) error {
	if !modeIsCachetag(cfg.mode) {
		return fmt.Errorf("phase5-held-publication requires cachetag mode, got %s", cfg.mode)
	}
	if cfg.phaseMarkerDir == "" {
		return fmt.Errorf("phase5-held-publication requires BENCH_PHASE_MARKER_DIR")
	}
	if err := runLoadObjectsPhase(client, baseURL, cfg, lines); err != nil {
		return err
	}
	beginPhase(lines, "load-residency")
	if err := validateResidentHits(client, baseURL, cfg, 0, cfg.objects, lines, "driver_load"); err != nil {
		return err
	}
	if err := runWarmHits(client, baseURL, cfg, lines); err != nil {
		return err
	}
	holdStart := beginPhase(lines, "phase5-hold-fetch")
	if err := writePhaseMarker(cfg, "phase5-hold-fetch", "start"); err != nil {
		return err
	}
	var heldDone chan error
	if cfg.phase5HoldPublication {
		heldDone = make(chan error, 1)
		go func() {
			heldDone <- heldPublicationRequest(client, baseURL)
		}()
		waitStart := time.Now()
		if err := waitForPhaseSignal(cfg, "phase5-hold", "active", time.Duration(cfg.httpTimeout)*time.Second); err != nil {
			return err
		}
		lines.add("driver_phase5_hold_active_wait_seconds", time.Since(waitStart).Seconds())
	} else {
		if err := writePhaseMarker(cfg, "phase5-hold", "active"); err != nil {
			return err
		}
		lines.add("driver_phase5_hold_active_wait_seconds", 0)
	}

	purgeStart := beginPhase(lines, "phase5-initial-purge")
	expected := expectedCount(cfg, cfg.purgeKey)
	purged, err := purge(client, baseURL, cfg.purgeKey, expected, false, true)
	lines.add("driver_phase5_initial_purge_key", cfg.purgeKey)
	lines.add("driver_phase5_initial_purge_expected", expected)
	lines.add("driver_phase5_initial_purge_actual", purged)
	lines.add("driver_phase5_initial_purge_published", purged == -1)
	recordPhaseSeconds(lines, "phase5-initial-purge", purgeStart)
	if err != nil {
		return err
	}
	acceptedPurgeEpoch(cfg, purged)
	compactStart := beginPhase(lines, "phase5-held-compact")
	compacted, err := compactRequest(client, baseURL)
	lines.add("driver_phase5_held_compact_returned", compacted)
	recordPhaseSeconds(lines, "phase5-held-compact", compactStart)
	if err != nil {
		return err
	}
	if err := runPhase5HeldTraffic(client, baseURL, cfg, lines); err != nil {
		return err
	}
	if cfg.phase5Shutdown {
		if err := writePhaseMarker(cfg, "phase5-shutdown", "ready"); err != nil {
			return err
		}
		if err := waitForPhaseSignal(cfg, "phase5-shutdown", "release",
			time.Duration(cfg.httpTimeout)*time.Second); err != nil {
			return err
		}
		if err := writePhaseMarker(cfg, "phase5-hold-release", "start"); err != nil {
			return err
		}
		if err := <-heldDone; err != nil {
			return err
		}
		if err := writePhaseMarker(cfg, "phase5-hold-release", "end"); err != nil {
			return err
		}
		recordPhaseSeconds(lines, "phase5-hold-fetch", holdStart)
		return nil
	}
	if err := writePhaseMarker(cfg, "phase5-hold-release", "start"); err != nil {
		return err
	}
	if cfg.phase5HoldPublication {
		if err := <-heldDone; err != nil {
			return err
		}
	}
	if err := writePhaseMarker(cfg, "phase5-hold-release", "end"); err != nil {
		return err
	}
	recordPhaseSeconds(lines, "phase5-hold-fetch", holdStart)
	releaseCompactStart := beginPhase(lines, "phase5-release-compact")
	releaseCompacted, err := compactRequest(client, baseURL)
	lines.add("driver_phase5_release_compact_returned", releaseCompacted)
	recordPhaseSeconds(lines, "phase5-release-compact", releaseCompactStart)
	if err != nil {
		return err
	}
	if err := waitForPendingZero(client, baseURL, cfg); err != nil {
		return err
	}
	return validatePurgeWindow(client, baseURL, cfg, cfg.purgeKey, lines, "driver_phase5", false)
}

func runConcurrent(client *http.Client, baseURL string, cfg config, lines *metrics) error {
	if err := runLoadObjectsPhase(client, baseURL, cfg, lines); err != nil {
		return err
	}
	beginPhase(lines, "load-residency")
	if err := validateResidentHits(client, baseURL, cfg, 0, cfg.objects, lines, "driver_load"); err != nil {
		return err
	}
	if err := runWarmHits(client, baseURL, cfg, lines); err != nil {
		return err
	}
	concurrentStart := beginPhase(lines, "concurrent")
	if err := writePhaseMarker(cfg, "concurrent", "start"); err != nil {
		return err
	}
	deadline := time.Now().Add(time.Duration(cfg.concurrentSeconds) * time.Second)
	secondStats := newConcurrentSecondStats(cfg.concurrentSeconds)
	readLatencies := newLatencyRecorder(200000)
	readPurgeLatencies := newLatencyRecorder(200000)
	readNoPurgeLatencies := newLatencyRecorder(200000)
	insertLatencies := newLatencyRecorder(200000)
	insertPurgeLatencies := newLatencyRecorder(200000)
	insertNoPurgeLatencies := newLatencyRecorder(200000)
	var reads, readDuringPurge, readOutsidePurge int64
	var inserts, insertDuringPurge, insertOutsidePurge int64
	var purges, purgesQueued, errors int64
	var nextRead atomic.Int64
	var nextInsert atomic.Int64
	var purgeActive atomic.Int64
	var firstErr error
	var firstErrMu sync.Mutex
	gate := newOperationGate(cfg.concurrentTargetRPS)
	recordErr := func(err error) {
		if err == nil {
			return
		}
		atomic.AddInt64(&errors, 1)
		firstErrMu.Lock()
		if firstErr == nil {
			firstErr = err
		}
		firstErrMu.Unlock()
	}
	var wg sync.WaitGroup
	for worker := 0; worker < cfg.concurrentReaders; worker++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for time.Now().Before(deadline) {
				gate.wait()
				obj := int(nextRead.Add(1)) % cfg.objects
				t0 := time.Now()
				err := cacheRequest(client, baseURL, cfg, obj)
				elapsed := time.Since(t0)
				second := concurrentSecond(concurrentStart, cfg.concurrentSeconds)
				atomicMaxInt64(&secondStats.readMaxNsec[second], elapsed.Nanoseconds())
				readLatencies.add(elapsed)
				if purgeActive.Load() > 0 {
					readPurgeLatencies.add(elapsed)
					atomic.AddInt64(&readDuringPurge, 1)
				} else {
					readNoPurgeLatencies.add(elapsed)
					atomic.AddInt64(&readOutsidePurge, 1)
				}
				if err != nil {
					secondStats.errors[second].Add(1)
					recordErr(err)
				} else {
					atomic.AddInt64(&reads, 1)
					secondStats.reads[second].Add(1)
				}
			}
		}()
	}
	for worker := 0; worker < cfg.concurrentWriters; worker++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for time.Now().Before(deadline) {
				gate.wait()
				obj := cfg.objects + int(nextInsert.Add(1))
				t0 := time.Now()
				err := cacheRequest(client, baseURL, cfg, obj)
				elapsed := time.Since(t0)
				second := concurrentSecond(concurrentStart, cfg.concurrentSeconds)
				atomicMaxInt64(&secondStats.insertMaxNsec[second], elapsed.Nanoseconds())
				insertLatencies.add(elapsed)
				if purgeActive.Load() > 0 {
					insertPurgeLatencies.add(elapsed)
					atomic.AddInt64(&insertDuringPurge, 1)
				} else {
					insertNoPurgeLatencies.add(elapsed)
					atomic.AddInt64(&insertOutsidePurge, 1)
				}
				if err != nil {
					secondStats.errors[second].Add(1)
					recordErr(err)
				} else {
					atomic.AddInt64(&inserts, 1)
					secondStats.inserts[second].Add(1)
				}
			}
		}()
	}
	for worker := 0; worker < cfg.concurrentPurgers; worker++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			interval := time.Duration(cfg.concurrentPurgers) * time.Second / time.Duration(cfg.concurrentPurgeRate)
			if interval < time.Millisecond {
				interval = time.Millisecond
			}
			ticker := time.NewTicker(interval)
			defer ticker.Stop()
			for time.Now().Before(deadline) {
				<-ticker.C
				purgeActive.Add(1)
				t0 := time.Now()
				allowQueued := modeIsCachetag(cfg.mode)
				purged, err := purge(client, baseURL, cfg.purgeKey, 0, false, allowQueued)
				elapsed := time.Since(t0)
				second := concurrentSecond(concurrentStart, cfg.concurrentSeconds)
				atomicMaxInt64(&secondStats.purgeMaxNsec[second], elapsed.Nanoseconds())
				if err != nil {
					secondStats.errors[second].Add(1)
					recordErr(err)
				} else {
					atomic.AddInt64(&purges, 1)
					secondStats.purges[second].Add(1)
					if purged == -1 {
						atomic.AddInt64(&purgesQueued, 1)
					}
				}
				purgeActive.Add(-1)
			}
		}()
	}
	wg.Wait()
	markerErr := writePhaseMarker(cfg, "concurrent", "end")
	firstErrMu.Lock()
	err := firstErr
	firstErrMu.Unlock()
	lines.add("driver_concurrent_seconds", cfg.concurrentSeconds)
	lines.add("driver_concurrent_readers", cfg.concurrentReaders)
	lines.add("driver_concurrent_writers", cfg.concurrentWriters)
	lines.add("driver_concurrent_purgers", cfg.concurrentPurgers)
	lines.add("driver_concurrent_target_rps", cfg.concurrentTargetRPS)
	lines.add("driver_concurrent_purge_rate", cfg.concurrentPurgeRate)
	lines.add("driver_concurrent_insert_every", cfg.concurrentInsertEvery)
	lines.add("driver_concurrent_reads", reads)
	lines.add("driver_concurrent_read_attempts_during_purge", readDuringPurge)
	lines.add("driver_concurrent_read_attempts_outside_purge", readOutsidePurge)
	lines.add("driver_concurrent_inserts", inserts)
	lines.add("driver_concurrent_insert_attempts_during_purge", insertDuringPurge)
	lines.add("driver_concurrent_insert_attempts_outside_purge", insertOutsidePurge)
	lines.add("driver_concurrent_purges", purges)
	lines.add("driver_concurrent_purges_published", purgesQueued)
	lines.add("driver_concurrent_errors", errors)
	readLatencies.emit("driver_read", lines)
	readPurgeLatencies.emit("driver_read_during_purge", lines)
	readNoPurgeLatencies.emit("driver_read_outside_purge", lines)
	insertLatencies.emit("driver_insert", lines)
	insertPurgeLatencies.emit("driver_insert_during_purge", lines)
	insertNoPurgeLatencies.emit("driver_insert_outside_purge", lines)
	emitConcurrentSecondStats("driver_concurrent", secondStats, lines)
	recordPhaseSeconds(lines, "concurrent", concurrentStart)
	if err != nil {
		return err
	}
	return markerErr
}

func stormKey(cfg config, request int) (string, bool) {
	if cfg.purgeStormDistinct < 1 {
		return cfg.purgeKey, false
	}
	slot := request % cfg.purgeStormDistinct
	unknownCutoff := cfg.purgeStormDistinct * cfg.purgeStormUnknownPct / 100
	if slot < unknownCutoff {
		return fmt.Sprintf("storm:unknown:%d", slot), true
	}
	if cfg.buckets > 0 {
		return fmt.Sprintf("bucket:%d", slot%cfg.buckets), false
	}
	return cfg.purgeKey, false
}

func runPurgeStorm(client *http.Client, baseURL string, cfg config, lines *metrics) error {
	if err := runLoadObjectsPhase(client, baseURL, cfg, lines); err != nil {
		return err
	}
	beginPhase(lines, "load-residency")
	if err := validateResidentHits(client, baseURL, cfg, 0, cfg.objects, lines, "driver_load"); err != nil {
		return err
	}
	if err := runWarmHits(client, baseURL, cfg, lines); err != nil {
		return err
	}
	start := beginPhase(lines, "purge-storm")
	if err := writePhaseMarker(cfg, "purge-storm", "start"); err != nil {
		return err
	}
	deadline := time.Now().Add(time.Duration(cfg.concurrentSeconds) * time.Second)
	secondStats := newConcurrentSecondStats(cfg.concurrentSeconds)
	readLatencies := newLatencyRecorder(200000)
	purgeLatencies := newLatencyRecorder(200000)
	var reads, purges, purgesQueued, errors, unknownPurges, softPurges int64
	var nextRead atomic.Int64
	var nextPurge atomic.Int64
	var firstErr error
	var firstErrMu sync.Mutex
	readGate := newOperationGate(cfg.concurrentTargetRPS)
	purgeGate := newOperationGate(cfg.purgeStormRate)
	recordErr := func(err error) {
		if err == nil {
			return
		}
		atomic.AddInt64(&errors, 1)
		firstErrMu.Lock()
		if firstErr == nil {
			firstErr = err
		}
		firstErrMu.Unlock()
	}
	var wg sync.WaitGroup
	for worker := 0; worker < cfg.concurrentReaders; worker++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for time.Now().Before(deadline) {
				readGate.wait()
				obj := int(nextRead.Add(1)-1) % cfg.objects
				t0 := time.Now()
				err := cacheRequest(client, baseURL, cfg, obj)
				elapsed := time.Since(t0)
				second := concurrentSecond(start, cfg.concurrentSeconds)
				readLatencies.add(elapsed)
				atomicMaxInt64(&secondStats.readMaxNsec[second], elapsed.Nanoseconds())
				if err != nil {
					secondStats.errors[second].Add(1)
					recordErr(err)
				} else {
					atomic.AddInt64(&reads, 1)
					secondStats.reads[second].Add(1)
				}
			}
		}()
	}
	for worker := 0; worker < cfg.concurrentPurgers; worker++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for time.Now().Before(deadline) {
				purgeGate.wait()
				request := int(nextPurge.Add(1) - 1)
				key, unknown := stormKey(cfg, request)
				mode := "hard"
				if cfg.purgeStormSoftPct > 0 && request%100 < cfg.purgeStormSoftPct {
					mode = "soft"
					atomic.AddInt64(&softPurges, 1)
				}
				if unknown {
					atomic.AddInt64(&unknownPurges, 1)
				}
				t0 := time.Now()
				purged, err := purgeWithMode(client, baseURL, key, mode, 0, false, modeIsCachetag(cfg.mode))
				elapsed := time.Since(t0)
				second := concurrentSecond(start, cfg.concurrentSeconds)
				purgeLatencies.add(elapsed)
				atomicMaxInt64(&secondStats.purgeMaxNsec[second], elapsed.Nanoseconds())
				if err != nil {
					secondStats.errors[second].Add(1)
					recordErr(err)
				} else {
					atomic.AddInt64(&purges, 1)
					secondStats.purges[second].Add(1)
					if purged == -1 {
						atomic.AddInt64(&purgesQueued, 1)
					}
				}
			}
		}()
	}
	wg.Wait()
	markerErr := writePhaseMarker(cfg, "purge-storm", "end")
	firstErrMu.Lock()
	err := firstErr
	firstErrMu.Unlock()
	lines.add("driver_purge_storm_seconds", cfg.concurrentSeconds)
	lines.add("driver_purge_storm_readers", cfg.concurrentReaders)
	lines.add("driver_purge_storm_purgers", cfg.concurrentPurgers)
	lines.add("driver_purge_storm_rate", cfg.purgeStormRate)
	lines.add("driver_purge_storm_distinct", cfg.purgeStormDistinct)
	lines.add("driver_purge_storm_unknown_percent", cfg.purgeStormUnknownPct)
	lines.add("driver_purge_storm_soft_percent", cfg.purgeStormSoftPct)
	lines.add("driver_purge_storm_reads", reads)
	lines.add("driver_purge_storm_purges", purges)
	lines.add("driver_purge_storm_unknown_purges", unknownPurges)
	lines.add("driver_purge_storm_soft_purges", softPurges)
	lines.add("driver_purge_storm_purges_published", purgesQueued)
	lines.add("driver_purge_storm_errors", errors)
	if cfg.concurrentSeconds > 0 {
		lines.add("driver_purge_storm_read_requests_per_second", float64(reads)/float64(cfg.concurrentSeconds))
		lines.add("driver_purge_storm_purge_requests_per_second", float64(purges)/float64(cfg.concurrentSeconds))
	}
	readLatencies.emit("driver_purge_storm_read", lines)
	purgeLatencies.emit("driver_purge_storm_purge", lines)
	emitConcurrentSecondStats("driver_purge_storm", secondStats, lines)
	recordPhaseSeconds(lines, "purge-storm", start)
	if err != nil {
		return err
	}
	return markerErr
}

func runPopulatedMapWarm(client *http.Client, baseURL string, cfg config, lines *metrics) error {
	if err := runLoadObjectsPhase(client, baseURL, cfg, lines); err != nil {
		return err
	}
	beginPhase(lines, "load-residency")
	if err := validateResidentHits(client, baseURL, cfg, 0, cfg.objects, lines, "driver_load"); err != nil {
		return err
	}
	start := beginPhase(lines, "populate-map")
	latencies := newLatencyRecorder(200000)
	var inserted, published, retries int64
	for n := 0; n < cfg.populatedMapEntries; n++ {
		t0 := time.Now()
		purged, err := purge(client, baseURL, fmt.Sprintf("mapseed:%d", n), 0, false, modeIsCachetag(cfg.mode))
		if err != nil {
			client.CloseIdleConnections()
			purged, err = purge(client, baseURL, fmt.Sprintf("mapseed:%d", n), 0, false, modeIsCachetag(cfg.mode))
			if err == nil {
				retries++
			}
		}
		latencies.add(time.Since(t0))
		if err != nil {
			return err
		}
		inserted++
		if purged == -1 {
			published++
		}
	}
	lines.add("driver_populated_map_entries_requested", cfg.populatedMapEntries)
	lines.add("driver_populated_map_entries_inserted", inserted)
	lines.add("driver_populated_map_purges_published", published)
	lines.add("driver_populated_map_transport_retries", retries)
	if seconds := time.Since(start).Seconds(); seconds > 0 {
		lines.add("driver_populated_map_purges_per_second", float64(inserted)/seconds)
	}
	latencies.emit("driver_populated_map_purge", lines)
	latencyPath := latencySamplePath(cfg.metricsPath, "populate_map_purge")
	if err := writeIndexedLatencySamples(latencyPath, latencies.snapshot()); err != nil {
		return err
	}
	lines.add("driver_populated_map_latency_samples_path", latencyPath)
	recordPhaseSeconds(lines, "populate-map", start)
	return runWarmHits(client, baseURL, cfg, lines)
}

type stream1OverlapReadSample struct {
	index      int
	startNS    int64
	endNS      int64
	seconds    float64
	cacheState string
}

func writeStream1OverlapSamples(path string, samples []stream1OverlapReadSample, purgeStartNS int64, purgeEndNS int64) error {
	if path == "" {
		return nil
	}
	var b strings.Builder
	b.WriteString("request_index\tstart_unix_ns\tend_unix_ns\tseconds\trelation\tcache_state\n")
	for _, sample := range samples {
		relation := "pre"
		if sample.endNS >= purgeStartNS && sample.startNS <= purgeEndNS {
			relation = "overlap"
		} else if sample.startNS > purgeEndNS {
			relation = "post"
		}
		fmt.Fprintf(&b, "%d\t%d\t%d\t%.9f\t%s\t%s\n", sample.index, sample.startNS, sample.endNS, sample.seconds, relation, sample.cacheState)
	}
	return os.WriteFile(path, []byte(b.String()), 0644)
}

func runStream1CheckpointOverlap(client *http.Client, baseURL string, cfg config, lines *metrics) error {
	if !modeIsCachetag(cfg.mode) || !cfg.cacheTagPersist {
		return fmt.Errorf("stream1-checkpoint-overlap requires persistent cachetag")
	}
	preseedStart := beginPhase(lines, "stream1-overlap-preseed")
	for n := 0; n < cfg.stream1OverlapPreseed; n++ {
		purged, err := purge(client, baseURL, fmt.Sprintf("stream1:overlap:seed:%d", n), 0, false, true)
		if err != nil {
			return err
		}
		if purged != -1 {
			return fmt.Errorf("stream1 preseed purge %d was not published: %d", n+1, purged)
		}
	}
	recordPhaseSeconds(lines, "stream1-overlap-preseed", preseedStart)
	if err := runExactLoadObjectsPhase(client, baseURL, cfg, lines); err != nil {
		return err
	}
	beginPhase(lines, "load-residency")
	if err := validateResidentHits(client, baseURL, cfg, 0, cfg.objects, lines, "driver_load"); err != nil {
		return err
	}

	readers := cfg.concurrentReaders
	if readers > cfg.stream1OverlapReads {
		readers = cfg.stream1OverlapReads
	}
	if readers < 1 {
		return fmt.Errorf("stream1 overlap requires at least one reader")
	}
	samples := make([]stream1OverlapReadSample, cfg.stream1OverlapReads)
	ready := make(chan struct{}, readers)
	readGo := make(chan struct{})
	purgeGo := make(chan struct{})
	purgeStarted := make(chan struct{})
	type stream1PurgeResult struct {
		published int
		err       error
		startNS   int64
		endNS     int64
	}
	purgeDone := make(chan stream1PurgeResult, 1)
	var firstErr error
	var firstErrMu sync.Mutex
	recordErr := func(err error) {
		if err == nil {
			return
		}
		firstErrMu.Lock()
		if firstErr == nil {
			firstErr = err
		}
		firstErrMu.Unlock()
	}
	var wg sync.WaitGroup
	for worker := 0; worker < readers; worker++ {
		wg.Add(1)
		go func(worker int) {
			defer wg.Done()
			obj := worker % cfg.objects
			primer, err := objectRequest(client, baseURL, cfg, obj)
			if err != nil {
				recordErr(fmt.Errorf("stream1 overlap primer reader %d: %w", worker, err))
			} else if primer.cacheState != "hit" {
				recordErr(fmt.Errorf("stream1 overlap primer reader %d was %q, expected hit", worker, primer.cacheState))
			}
			ready <- struct{}{}
			<-readGo
			for index := worker; index < cfg.stream1OverlapReads; index += readers {
				start := time.Now()
				response, err := objectRequest(client, baseURL, cfg, obj)
				end := time.Now()
				state := "error"
				if err != nil {
					recordErr(fmt.Errorf("stream1 overlap read %d: %w", index+1, err))
				} else {
					state = response.cacheState
					if state != "hit" {
						recordErr(fmt.Errorf("stream1 overlap read %d was %q, expected hit", index+1, state))
					}
				}
				samples[index] = stream1OverlapReadSample{index: index + 1, startNS: start.UnixNano(), endNS: end.UnixNano(), seconds: end.Sub(start).Seconds(), cacheState: state}
			}
		}(worker)
	}
	go func() {
		<-purgeGo
		start := time.Now()
		close(purgeStarted)
		published, err := purge(client, baseURL, "stream1:overlap:trigger", 0, false, true)
		end := time.Now()
		purgeDone <- stream1PurgeResult{published: published, err: err, startNS: start.UnixNano(), endNS: end.UnixNano()}
	}()
	for n := 0; n < readers; n++ {
		<-ready
	}
	phaseStart := beginPhase(lines, "stream1-checkpoint-overlap")
	close(purgeGo)
	<-purgeStarted
	close(readGo)
	wg.Wait()
	trigger := <-purgeDone
	recordPhaseSeconds(lines, "stream1-checkpoint-overlap", phaseStart)
	if trigger.err != nil {
		return trigger.err
	}
	if trigger.published != -1 {
		return fmt.Errorf("stream1 trigger purge was not published: %d", trigger.published)
	}
	firstErrMu.Lock()
	err := firstErr
	firstErrMu.Unlock()
	if err != nil {
		return err
	}
	latencies := newLatencyRecorder(len(samples))
	overlapReads := 0
	overlapOver15MS := 0
	totalOver15MS := 0
	for _, sample := range samples {
		latencies.add(time.Duration(sample.seconds * float64(time.Second)))
		if sample.seconds > 0.015 {
			totalOver15MS++
		}
		if sample.endNS >= trigger.startNS && sample.startNS <= trigger.endNS {
			overlapReads++
			if sample.seconds > 0.015 {
				overlapOver15MS++
			}
		}
	}
	if overlapReads == 0 {
		return fmt.Errorf("stream1 overlap lane captured no read overlapping the trigger purge")
	}
	path := latencySamplePath(cfg.metricsPath, "stream1_overlap_reads")
	if err := writeStream1OverlapSamples(path, samples, trigger.startNS, trigger.endNS); err != nil {
		return err
	}
	lines.add("driver_stream1_overlap_preseed_entries", cfg.stream1OverlapPreseed)
	lines.add("driver_stream1_overlap_reads", len(samples))
	lines.add("driver_stream1_overlap_readers", readers)
	lines.add("driver_stream1_overlap_reads_during_purge", overlapReads)
	lines.add("driver_stream1_overlap_reads_over_15ms", totalOver15MS)
	lines.add("driver_stream1_overlap_reads_during_purge_over_15ms", overlapOver15MS)
	lines.add("driver_stream1_overlap_purge_start_unix_ns", trigger.startNS)
	lines.add("driver_stream1_overlap_purge_end_unix_ns", trigger.endNS)
	lines.add("driver_stream1_overlap_purge_seconds", float64(trigger.endNS-trigger.startNS)/float64(time.Second))
	lines.add("driver_stream1_overlap_latency_samples_path", path)
	latencies.emit("driver_stream1_overlap_read", lines)
	return nil
}

func runColdResidencySweep(client *http.Client, baseURL string, cfg config, purgeReturnedAt time.Time, lines *metrics) error {
	start := beginPhase(lines, "cold-residency-sweep")
	if err := writePhaseMarker(cfg, "cold-residency-sweep", "start"); err != nil {
		return err
	}
	deadline := time.Now().Add(time.Duration(cfg.residencySweepSeconds) * time.Second)
	secondStats := newConcurrentSecondStats(cfg.residencySweepSeconds)
	readLatencies := newLatencyRecorder(200000)
	readGate := newOperationGate(cfg.concurrentTargetRPS)
	sampleEvery := time.Duration(cfg.residencySampleMS) * time.Millisecond
	if sampleEvery <= 0 {
		sampleEvery = time.Second
	}
	var reads, errors int64
	var nextRead atomic.Int64
	var firstErr error
	var firstErrMu sync.Mutex
	recordErr := func(err error) {
		if err == nil {
			return
		}
		atomic.AddInt64(&errors, 1)
		firstErrMu.Lock()
		if firstErr == nil {
			firstErr = err
		}
		firstErrMu.Unlock()
	}
	var wg sync.WaitGroup
	for worker := 0; worker < cfg.concurrentReaders; worker++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for time.Now().Before(deadline) {
				readGate.wait()
				if !time.Now().Before(deadline) {
					return
				}
				obj := int(nextRead.Add(1)-1) % cfg.objects
				t0 := time.Now()
				err := cacheRequest(client, baseURL, cfg, obj)
				elapsed := time.Since(t0)
				second := concurrentSecond(start, cfg.residencySweepSeconds)
				readLatencies.add(elapsed)
				atomicMaxInt64(&secondStats.readMaxNsec[second], elapsed.Nanoseconds())
				if err != nil {
					secondStats.errors[second].Add(1)
					recordErr(err)
				} else {
					atomic.AddInt64(&reads, 1)
					secondStats.reads[second].Add(1)
				}
			}
		}()
	}
	samples := 0
	lastObjects := -1
	minObjects := math.MaxInt64
	maxObjects := 0
	if modeIsCachetag(cfg.mode) {
		for {
			objects, err := objectCountRequest(client, baseURL)
			if err != nil {
				recordErr(err)
				break
			}
			lines.add(fmt.Sprintf("driver_cold_residency_sample_%03d_ms", samples), time.Since(purgeReturnedAt).Milliseconds())
			lines.add(fmt.Sprintf("driver_cold_residency_sample_%03d_objects", samples), objects)
			lastObjects = objects
			if objects < minObjects {
				minObjects = objects
			}
			if objects > maxObjects {
				maxObjects = objects
			}
			samples++
			if !time.Now().Before(deadline) {
				break
			}
			time.Sleep(sampleEvery)
		}
		if minObjects == math.MaxInt64 {
			minObjects = 0
		}
		lines.add("driver_cold_residency_sample_seconds", cfg.residencySweepSeconds)
		lines.add("driver_cold_residency_sample_interval_ms", cfg.residencySampleMS)
		lines.add("driver_cold_residency_samples", samples)
		lines.add("driver_cold_residency_objects_last", lastObjects)
		lines.add("driver_cold_residency_objects_min", minObjects)
		lines.add("driver_cold_residency_objects_max", maxObjects)
	} else {
		lines.add("driver_cold_residency_sample", "unsupported")
		time.Sleep(time.Until(deadline))
	}
	wg.Wait()
	markerErr := writePhaseMarker(cfg, "cold-residency-sweep", "end")
	lines.add("driver_cold_residency_sweep_seconds", cfg.residencySweepSeconds)
	lines.add("driver_cold_residency_sweep_readers", cfg.concurrentReaders)
	lines.add("driver_cold_residency_sweep_target_rps", cfg.concurrentTargetRPS)
	lines.add("driver_cold_residency_sweep_reads", reads)
	lines.add("driver_cold_residency_sweep_errors", errors)
	if cfg.residencySweepSeconds > 0 {
		lines.add("driver_cold_residency_sweep_read_requests_per_second", float64(reads)/float64(cfg.residencySweepSeconds))
	}
	readLatencies.emit("driver_cold_residency_sweep_read", lines)
	emitConcurrentSecondStats("driver_cold_residency_sweep", secondStats, lines)
	recordPhaseSeconds(lines, "cold-residency-sweep", start)
	firstErrMu.Lock()
	err := firstErr
	firstErrMu.Unlock()
	if err != nil {
		return err
	}
	return markerErr
}

func runPurgedColdResidency(client *http.Client, baseURL string, cfg config, lines *metrics) error {
	if err := runLoadObjectsPhase(client, baseURL, cfg, lines); err != nil {
		return err
	}
	beginPhase(lines, "load-residency")
	if err := validateResidentHits(client, baseURL, cfg, 0, cfg.objects, lines, "driver_load"); err != nil {
		return err
	}
	key := cfg.purgeKey
	expected := expectedCount(cfg, key)
	purgeStart := beginPhase(lines, "cold-residency-purge")
	purged, err := purge(client, baseURL, key, expected, true, modeIsCachetag(cfg.mode))
	purgeReturnedAt := time.Now()
	lines.add("driver_cold_residency_purge_key", key)
	lines.add("driver_cold_residency_purge_expected", expected)
	lines.add("driver_cold_residency_purge_actual", purged)
	lines.add("driver_cold_residency_purge_published", purged == -1)
	recordPhaseSeconds(lines, "cold-residency-purge", purgeStart)
	if err != nil {
		return err
	}
	lines.add("driver_cold_residency_origin_epoch", acceptedPurgeEpoch(cfg, purged))
	if err := validatePurgeWindow(client, baseURL, cfg, key, lines, "driver_cold_residency", true); err != nil {
		return err
	}
	if err := runColdResidencySweep(client, baseURL, cfg, purgeReturnedAt, lines); err != nil {
		return err
	}
	return nil
}

func writeMetrics(path string, lines metrics) error {
	data := ""
	for _, line := range lines {
		data += line + "\n"
	}
	return os.WriteFile(path, []byte(data), 0o644)
}

func emitTagShapeMetrics(cfg config, lines *metrics) error {
	objectIDs := tagShapeSampleObjectIDs(cfg.objects)
	samples := len(objectIDs)
	minTags := math.MaxInt32
	maxTags := 0
	minLen := math.MaxInt32
	maxLen := 0
	unique := make(map[string]struct{})
	for _, obj := range objectIDs {
		tags := tagsFor(cfg, obj)
		if len(tags) < minTags {
			minTags = len(tags)
		}
		if len(tags) > maxTags {
			maxTags = len(tags)
		}
		for _, tag := range tags {
			if len(tag) < minLen {
				minLen = len(tag)
			}
			if len(tag) > maxLen {
				maxLen = len(tag)
			}
			unique[tag] = struct{}{}
		}
	}
	if samples == 0 {
		minTags = 0
	}
	if len(unique) == 0 {
		minLen = 0
	}
	lines.add("driver_tag_length_class", cfg.tagLengthClass)
	lines.add("driver_tag_shape_sample_objects", samples)
	lines.add("driver_tag_shape_min_tags_per_object", minTags)
	lines.add("driver_tag_shape_max_tags_per_object", maxTags)
	lines.add("driver_tag_shape_min_tag_length", minLen)
	lines.add("driver_tag_shape_max_tag_length", maxLen)
	lines.add("driver_tag_shape_sample_unique_tags", len(unique))
	lines.add("driver_tag_shape_validation_configured", boolInt(cfg.validateTagShape))
	lines.add("driver_tag_shape_expected_tags_per_object", cfg.tagsPerObject)

	validationOK := true
	reasons := []string{}
	if minTags != cfg.tagsPerObject || maxTags != cfg.tagsPerObject {
		validationOK = false
		reasons = append(
			reasons,
			fmt.Sprintf("tags_per_object observed %d-%d expected %d", minTags, maxTags, cfg.tagsPerObject),
		)
	}

	lengthChecked := profileUsesCutoverTagClass(cfg.profile)
	lines.add("driver_tag_shape_length_class_checked", boolInt(lengthChecked))
	if lengthChecked {
		expectedMinLen, expectedMaxLen := cutoverTagLengthBounds(cfg.tagLengthClass)
		lines.add("driver_tag_shape_expected_min_tag_length", expectedMinLen)
		lines.add("driver_tag_shape_expected_max_tag_length", expectedMaxLen)
		if minLen < expectedMinLen || maxLen > expectedMaxLen {
			validationOK = false
			reasons = append(
				reasons,
				fmt.Sprintf(
					"tag_length observed %d-%d expected %d-%d for class %s",
					minLen,
					maxLen,
					expectedMinLen,
					expectedMaxLen,
					cfg.tagLengthClass,
				),
			)
		}
	} else {
		lines.add("driver_tag_shape_expected_min_tag_length", 0)
		lines.add("driver_tag_shape_expected_max_tag_length", 0)
	}

	expectedUnique, uniqueChecked := expectedCutoverSampleUniqueTags(
		cfg.profile,
		samples,
		cfg.tagsPerObject,
	)
	lines.add("driver_tag_shape_unique_count_checked", boolInt(uniqueChecked))
	lines.add("driver_tag_shape_expected_sample_unique_tags", expectedUnique)
	if uniqueChecked && len(unique) != expectedUnique {
		validationOK = false
		reasons = append(
			reasons,
			fmt.Sprintf("sample_unique_tags observed %d expected %d", len(unique), expectedUnique),
		)
	}

	lines.add("driver_tag_shape_validation_ok", boolInt(validationOK))
	if validationOK {
		lines.add("driver_tag_shape_validation_reason", "ok")
	} else {
		lines.add("driver_tag_shape_validation_reason", strings.Join(reasons, "; "))
	}
	if cfg.validateTagShape && !validationOK {
		return fmt.Errorf("tag shape validation failed: %s", strings.Join(reasons, "; "))
	}
	return nil
}

func main() {
	cfg, err := parseConfig()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	cfg.originEpoch = newOriginEpochController()
	maxConns := cfg.clients + cfg.concurrentReaders + cfg.concurrentWriters + cfg.concurrentPurgers + 4
	transport := &http.Transport{
		MaxIdleConns:        maxConns * 2,
		MaxIdleConnsPerHost: maxConns * 2,
		MaxConnsPerHost:     maxConns,
		IdleConnTimeout:     90 * time.Second,
		DisableKeepAlives:   cfg.disableKeepAlives,
	}
	defer transport.CloseIdleConnections()
	client := &http.Client{
		Transport: transport,
		Timeout:   time.Duration(cfg.httpTimeout) * time.Second,
	}
	baseURL := makeBaseURL(cfg.host, cfg.port)
	before := getUsage()
	t0 := time.Now()
	lines := metrics{}
	lines.add("driver_implementation", "go")
	lines.add("driver_mode", cfg.mode)
	lines.add("driver_profile", cfg.profile)
	lines.add("driver_clients", cfg.clients)
	lines.add("driver_bucket_modulus", cfg.buckets)
	lines.add("driver_tags_per_object", cfg.tagsPerObject)
	lines.add("driver_warm_seconds_configured", cfg.warmSeconds)
	lines.add("driver_warm_validate_hit_configured", cfg.warmValidateHit)
	lines.add("driver_allow_stale_after_purge", cfg.allowStaleAfterPurge)
	lines.add("driver_validate_residency", cfg.validateResidency)
	lines.add("driver_residency_validate_objects", cfg.residencyValidate)
	lines.add("driver_disable_keepalives", cfg.disableKeepAlives)
	lines.add("driver_storage_kind", cfg.storageKind)
	lines.add("driver_cachetag_persist", cfg.cacheTagPersist)
	lines.add("driver_phase_marker_dir", cfg.phaseMarkerDir)
	lines.add("driver_phase_marker_prefix", cfg.phaseMarkerPrefix)
	lines.add("driver_purge_storm_rate_configured", cfg.purgeStormRate)
	lines.add("driver_purge_storm_distinct_configured", cfg.purgeStormDistinct)
	lines.add("driver_purge_storm_unknown_percent_configured", cfg.purgeStormUnknownPct)
	lines.add("driver_purge_storm_soft_percent_configured", cfg.purgeStormSoftPct)
	lines.add("driver_populated_map_entries_configured", cfg.populatedMapEntries)
	lines.add("driver_stream1_overlap_preseed_entries_configured", cfg.stream1OverlapPreseed)
	lines.add("driver_stream1_overlap_reads_configured", cfg.stream1OverlapReads)
	lines.add("driver_residency_sweep_seconds_configured", cfg.residencySweepSeconds)
	lines.add("driver_residency_sample_ms_configured", cfg.residencySampleMS)
	lines.add("driver_phase4_pre_seconds_configured", cfg.phase4PreSeconds)
	lines.add("driver_phase4_sweep_seconds_configured", cfg.phase4SweepSeconds)
	lines.add("driver_phase4_post_seconds_configured", cfg.phase4PostSeconds)
	lines.add("driver_phase4_attribution_guard_ms_configured", cfg.phase4GuardMS)
	lines.add("driver_phase5_hold_ms_configured", cfg.phase5HoldMS)
	lines.add("driver_phase5_cap_purges_configured", cfg.phase5CapPurges)
	lines.add("driver_phase5_hold_publication", boolInt(cfg.phase5HoldPublication))
	lines.add("driver_phase5_shutdown", boolInt(cfg.phase5Shutdown))
	lines.add("driver_phase6_pressure_body_bytes_configured", cfg.phase6PressureBody)
	lines.add("driver_phase6_quiet_seconds_configured", cfg.phase6QuietSeconds)
	lines.add("driver_origin_epoch_initial", cfg.originEpoch.current())
	err = emitTagShapeMetrics(cfg, &lines)

	if err == nil {
		phase := modePhase(cfg.mode)
		switch phase {
		case "noindex", "load":
			err = runLoad(client, baseURL, cfg, &lines)
		case "eviction":
			err = runEviction(client, baseURL, cfg, &lines)
		case "purge":
			err = runPurge(client, baseURL, cfg, &lines, true)
		case "purge-noexact":
			err = runPurge(client, baseURL, cfg, &lines, false)
		case "short-ttl-high-churn":
			err = runShortTTL(client, baseURL, cfg, &lines)
		case "rotating-tag-churn":
			err = runShortTTL(client, baseURL, cfg, &lines)
		case "rotating-tag-churn-deterministic-full":
			err = runShortTTL(client, baseURL, cfg, &lines)
		case "rotating-tag-churn-deterministic-incremental":
			err = runShortTTL(client, baseURL, cfg, &lines)
		case "bulk-purge-bursts":
			err = runBulkPurge(client, baseURL, cfg, &lines)
		case "concurrent":
			err = runConcurrent(client, baseURL, cfg, &lines)
		case "purge-storm":
			err = runPurgeStorm(client, baseURL, cfg, &lines)
		case "purged-cold-residency":
			err = runPurgedColdResidency(client, baseURL, cfg, &lines)
		case "populated-map-warm":
			err = runPopulatedMapWarm(client, baseURL, cfg, &lines)
		case "stream1-checkpoint-overlap":
			err = runStream1CheckpointOverlap(client, baseURL, cfg, &lines)
		case "phase4-sweep-latency", "phase4-refill-control":
			err = runPhase4SweepLatency(client, baseURL, cfg, &lines)
		case "phase6-fill-drain":
			if modeIsNoindex(cfg.mode) {
				err = runNoindexPhase6BanDrain(client, baseURL, cfg, &lines)
			} else {
				err = runPhase6FillDrain(client, baseURL, cfg, &lines)
			}
		case "phase5-held-short", "phase5-held-multi", "phase5-held-cap", "phase5-held-shutdown", "phase5-nohold-short", "phase5-nohold-multi", "phase5-nohold-cap":
			err = runPhase5HeldPublication(client, baseURL, cfg, &lines)
		default:
			err = fmt.Errorf("unsupported mode: %s", cfg.mode)
		}
	}
	after := getUsage()
	lines.add("driver_wall_seconds", time.Since(t0).Seconds())
	lines.add("driver_user_seconds", usageSeconds(after.Utime)-usageSeconds(before.Utime))
	lines.add("driver_system_seconds", usageSeconds(after.Stime)-usageSeconds(before.Stime))
	if err != nil {
		lines.add("driver_errors", 1)
		lines.add("driver_error", err.Error())
	} else {
		lines.add("driver_errors", 0)
	}
	if err := writeMetrics(cfg.metricsPath, lines); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	for _, line := range lines {
		fmt.Println(line)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
