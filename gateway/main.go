// ZeroSpoof ingestion gateway.
//
// A small, fast WebSocket reverse proxy that sits between thousands of browsers and the Python
// inference service. It does the cheap, concurrency-heavy work in Go so Python only sees clean traffic:
//
//   - accepts browser connections on /ws?token=...
//   - checks the Origin against an allow-list (ZS_ALLOWED_ORIGINS)
//   - caps concurrent connections, message size and per-connection message rate
//   - dials the inference service and pumps messages both ways (one goroutine per direction)
//   - keeps connections alive with ping/pong and closes dead peers
//   - exposes /healthz and /metrics (JSON counters)
//
// Authentication is NOT done here: the signed one-time token travels to the inference service,
// which verifies it (HMAC, expiry, single use).
package main

import (
	"context"
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/gorilla/websocket"
)

type config struct {
	listen         string
	upstream       string // ws://inference:8000/ws
	allowedOrigins []string
	maxConns       int64
	maxMsgBytes    int64
	maxMsgsPerSec  int
	pingEvery      time.Duration
}

func env(name, def string) string {
	if v := os.Getenv(name); v != "" {
		return v
	}
	return def
}

func envInt(name string, def int) int {
	if v, err := strconv.Atoi(os.Getenv(name)); err == nil {
		return v
	}
	return def
}

func loadConfig() config {
	var origins []string
	for _, o := range strings.Split(env("ZS_ALLOWED_ORIGINS", "http://localhost:3000"), ",") {
		if o = strings.TrimSpace(o); o != "" {
			origins = append(origins, o)
		}
	}
	return config{
		listen:         env("GATEWAY_LISTEN", ":8080"),
		upstream:       env("UPSTREAM_WS", "ws://localhost:8000/ws"),
		allowedOrigins: origins,
		maxConns:       int64(envInt("GATEWAY_MAX_CONNS", 5000)),
		maxMsgBytes:    int64(envInt("GATEWAY_MAX_MSG_BYTES", 512*1024)),
		maxMsgsPerSec:  envInt("GATEWAY_MAX_MSGS_PER_SEC", 200),
		pingEvery:      20 * time.Second,
	}
}

type metrics struct {
	active, total, rejected     atomic.Int64
	msgsUp, msgsDown            atomic.Int64
	bytesUp, bytesDown          atomic.Int64
	upstreamFailures, rateLimit atomic.Int64
}

type gateway struct {
	cfg      config
	m        metrics
	upgrader websocket.Upgrader
}

func (g *gateway) originAllowed(r *http.Request) bool {
	origin := r.Header.Get("Origin")
	if origin == "" {
		return true // non-browser client (the simulator, server-to-server); the token still has to be valid
	}
	for _, o := range g.cfg.allowedOrigins {
		if o == "*" || strings.EqualFold(o, origin) {
			return true
		}
	}
	return false
}

// tokenBucket is a tiny per-connection rate limiter.
type tokenBucket struct {
	mu       sync.Mutex
	tokens   float64
	capacity float64
	rate     float64
	last     time.Time
}

func newBucket(perSec int) *tokenBucket {
	return &tokenBucket{tokens: float64(perSec), capacity: float64(perSec), rate: float64(perSec), last: time.Now()}
}

func (b *tokenBucket) allow() bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	now := time.Now()
	b.tokens += now.Sub(b.last).Seconds() * b.rate
	if b.tokens > b.capacity {
		b.tokens = b.capacity
	}
	b.last = now
	if b.tokens < 1 {
		return false
	}
	b.tokens--
	return true
}

func (g *gateway) handleWS(w http.ResponseWriter, r *http.Request) {
	if g.m.active.Load() >= g.cfg.maxConns {
		g.m.rejected.Add(1)
		http.Error(w, "gateway at capacity", http.StatusServiceUnavailable)
		return
	}
	if r.URL.Query().Get("token") == "" {
		g.m.rejected.Add(1)
		http.Error(w, "missing token", http.StatusUnauthorized)
		return
	}
	if !g.originAllowed(r) {
		g.m.rejected.Add(1)
		http.Error(w, "origin not allowed", http.StatusForbidden)
		return
	}

	// Dial upstream first, so a dead backend produces a clean error instead of a half-open socket.
	up, err := url.Parse(g.cfg.upstream)
	if err != nil {
		http.Error(w, "bad upstream", http.StatusInternalServerError)
		return
	}
	up.RawQuery = r.URL.RawQuery
	dialer := websocket.Dialer{HandshakeTimeout: 5 * time.Second}
	upConn, _, err := dialer.Dial(up.String(), nil)
	if err != nil {
		g.m.upstreamFailures.Add(1)
		log.Printf("upstream dial failed: %v", err)
		http.Error(w, "inference service unavailable", http.StatusBadGateway)
		return
	}

	client, err := g.upgrader.Upgrade(w, r, nil)
	if err != nil {
		upConn.Close()
		return
	}
	g.m.active.Add(1)
	g.m.total.Add(1)
	defer g.m.active.Add(-1)

	client.SetReadLimit(g.cfg.maxMsgBytes)
	upConn.SetReadLimit(4 << 20)

	ctx, cancel := context.WithCancel(r.Context())
	defer cancel()
	var once sync.Once
	closeBoth := func(code int, reason string) {
		once.Do(func() {
			msg := websocket.FormatCloseMessage(code, reason)
			deadline := time.Now().Add(time.Second)
			_ = client.WriteControl(websocket.CloseMessage, msg, deadline)
			_ = upConn.WriteControl(websocket.CloseMessage, websocket.FormatCloseMessage(websocket.CloseNormalClosure, ""), deadline)
			cancel()
			client.Close()
			upConn.Close()
		})
	}

	// keep-alive on the client side
	const pongWait = 60 * time.Second
	_ = client.SetReadDeadline(time.Now().Add(pongWait))
	client.SetPongHandler(func(string) error { return client.SetReadDeadline(time.Now().Add(pongWait)) })
	var writeMu sync.Mutex // gorilla allows one concurrent writer per connection

	go func() {
		t := time.NewTicker(g.cfg.pingEvery)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				writeMu.Lock()
				err := client.WriteControl(websocket.PingMessage, nil, time.Now().Add(5*time.Second))
				writeMu.Unlock()
				if err != nil {
					closeBoth(websocket.CloseGoingAway, "ping failed")
					return
				}
			}
		}
	}()

	// upstream -> client
	go func() {
		for {
			mt, data, err := upConn.ReadMessage()
			if err != nil {
				code, reason := websocket.CloseNormalClosure, ""
				var ce *websocket.CloseError
				if errors.As(err, &ce) {
					code, reason = ce.Code, ce.Text
				}
				closeBoth(code, reason)
				return
			}
			g.m.msgsDown.Add(1)
			g.m.bytesDown.Add(int64(len(data)))
			writeMu.Lock()
			_ = client.SetWriteDeadline(time.Now().Add(10 * time.Second))
			err = client.WriteMessage(mt, data)
			writeMu.Unlock()
			if err != nil {
				closeBoth(websocket.CloseGoingAway, "client write failed")
				return
			}
		}
	}()

	// client -> upstream (runs in this goroutine)
	bucket := newBucket(g.cfg.maxMsgsPerSec)
	for {
		mt, data, err := client.ReadMessage()
		if err != nil {
			closeBoth(websocket.CloseNormalClosure, "")
			return
		}
		_ = client.SetReadDeadline(time.Now().Add(pongWait))
		if !bucket.allow() {
			g.m.rateLimit.Add(1)
			closeBoth(4429, "rate limit")
			return
		}
		g.m.msgsUp.Add(1)
		g.m.bytesUp.Add(int64(len(data)))
		_ = upConn.SetWriteDeadline(time.Now().Add(10 * time.Second))
		if err := upConn.WriteMessage(mt, data); err != nil {
			closeBoth(websocket.CloseTryAgainLater, "upstream write failed")
			return
		}
	}
}

func (g *gateway) handleMetrics(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]int64{
		"connections_active":   g.m.active.Load(),
		"connections_total":    g.m.total.Load(),
		"connections_rejected": g.m.rejected.Load(),
		"messages_up":          g.m.msgsUp.Load(),
		"messages_down":        g.m.msgsDown.Load(),
		"bytes_up":             g.m.bytesUp.Load(),
		"bytes_down":           g.m.bytesDown.Load(),
		"upstream_failures":    g.m.upstreamFailures.Load(),
		"rate_limited":         g.m.rateLimit.Load(),
	})
}

func main() {
	cfg := loadConfig()
	g := &gateway{cfg: cfg}
	g.upgrader = websocket.Upgrader{
		ReadBufferSize:  16 * 1024,
		WriteBufferSize: 16 * 1024,
		CheckOrigin:     g.originAllowed,
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/ws", g.handleWS)
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write([]byte("ok")) })
	mux.HandleFunc("/metrics", g.handleMetrics)

	srv := &http.Server{Addr: cfg.listen, Handler: mux, ReadHeaderTimeout: 10 * time.Second}
	go func() {
		log.Printf("ZeroSpoof gateway listening on %s -> %s (origins: %v)", cfg.listen, cfg.upstream, cfg.allowedOrigins)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatal(err)
		}
	}()

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop
	log.Println("shutting down...")
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = srv.Shutdown(ctx)
}
