package main

import (
	"encoding/json"
	"io"
	"net/http"
	"sync"
	"time"
)

// UapiClient 带多密钥轮换与 30 分钟冷却的 JSON 请求客户端。
type UapiClient struct {
	cfg      *Config
	hc       *http.Client
	mu       sync.Mutex
	cooldown map[string]time.Time
}

func newUapiClient(cfg *Config) *UapiClient {
	timeout := time.Duration(cfg.HTTPTimeout * float64(time.Second))
	if timeout <= 0 {
		timeout = 8 * time.Second
	}
	return &UapiClient{
		cfg:      cfg,
		hc:       &http.Client{Timeout: timeout},
		cooldown: map[string]time.Time{},
	}
}

func (u *UapiClient) nextKey() string {
	u.mu.Lock()
	defer u.mu.Unlock()
	now := time.Now()
	for _, key := range u.cfg.UAPIKeys {
		if until, ok := u.cooldown[key]; !ok || now.After(until) {
			return key
		}
	}
	return ""
}

func (u *UapiClient) cool(key string) {
	if key == "" {
		return
	}
	u.mu.Lock()
	u.cooldown[key] = time.Now().Add(30 * time.Minute)
	u.mu.Unlock()
}

// getJSON 请求 JSON 接口，超时重试一次；返回对象与状态码。
func (u *UapiClient) getJSON(rawURL string) (map[string]any, int) {
	var lastStatus int
	for attempt := 0; attempt < 2; attempt++ {
		req, err := http.NewRequest(http.MethodGet, rawURL, nil)
		if err != nil {
			return nil, 0
		}
		req.Header.Set("User-Agent", "NewEndPage/0.1")
		req.Header.Set("Accept", "application/json")
		key := u.nextKey()
		if key != "" {
			req.Header.Set("Authorization", "Bearer "+key)
		}
		resp, err := u.hc.Do(req)
		if err != nil {
			continue
		}
		lastStatus = resp.StatusCode
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
		resp.Body.Close()
		if resp.StatusCode == 401 || resp.StatusCode == 402 {
			u.cool(key)
			continue
		}
		var payload map[string]any
		if err := json.Unmarshal(body, &payload); err != nil {
			return nil, resp.StatusCode
		}
		return payload, resp.StatusCode
	}
	return nil, lastStatus
}
