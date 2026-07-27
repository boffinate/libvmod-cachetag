package main

import (
	"bytes"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
)

func usage() {
	fmt.Fprintln(os.Stderr, "usage: http_backend HOST PORT BODY_BYTES")
	os.Exit(2)
}

func main() {
	if len(os.Args) != 4 {
		usage()
	}
	host := os.Args[1]
	port, err := strconv.Atoi(os.Args[2])
	if err != nil || port <= 0 {
		usage()
	}
	bodyBytes, err := strconv.Atoi(os.Args[3])
	if err != nil || bodyBytes < 0 {
		usage()
	}

	body := []byte(strings.Repeat("x", bodyBytes))
	addr := net.JoinHostPort(host, strconv.Itoa(port))
	listener, err := net.Listen("tcp", addr)
	if err != nil {
		log.Fatalf("listen %s: %v", addr, err)
	}
	fmt.Printf("ready %s\n", listener.Addr().String())

	server := &http.Server{
		Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			generation := uint64(1)
			if raw := r.Header.Get("X-Bench-Origin-Epoch"); raw != "" {
				parsed, parseErr := strconv.ParseUint(raw, 10, 64)
				if parseErr != nil || parsed == 0 {
					http.Error(w, fmt.Sprintf("invalid X-Bench-Origin-Epoch=%q", raw), http.StatusBadRequest)
					return
				}
				generation = parsed
			}
			responseBody := body
			if rawBodyBytes := r.Header.Get("X-Bench-Body-Bytes"); rawBodyBytes != "" {
				requestedBytes, parseErr := strconv.Atoi(rawBodyBytes)
				if parseErr != nil || requestedBytes < 0 || requestedBytes > 64*1024*1024 {
					http.Error(w, fmt.Sprintf("invalid X-Bench-Body-Bytes=%q", rawBodyBytes), http.StatusBadRequest)
					return
				}
				responseBody = bytes.Repeat([]byte{'x'}, requestedBytes)
			}
			w.Header().Set("Content-Length", strconv.Itoa(len(responseBody)))
			w.Header().Set("X-Origin-Generation", strconv.FormatUint(generation, 10))
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write(responseBody)
		}),
	}
	if err := server.Serve(listener); err != nil && err != http.ErrServerClosed {
		log.Fatalf("serve: %v", err)
	}
}
