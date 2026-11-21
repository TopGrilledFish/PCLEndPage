package main

import (
	"encoding/json"
	"os"
	"sync"
)

// PrefStore 记录哪些 IP 主动选择访问新版测试站点（不选择就保持旧版）。
// 文件格式：{"ips": {"1.2.3.4": true}}
type PrefStore struct {
	mu   sync.Mutex
	path string
	ips  map[string]bool
}

func newPrefStore(path string) *PrefStore {
	p := &PrefStore{path: path, ips: map[string]bool{}}
	p.load()
	return p
}

func (p *PrefStore) load() {
	raw, err := os.ReadFile(p.path)
	if err != nil {
		return
	}
	var data struct {
		Ips map[string]bool `json:"ips"`
	}
	if err := json.Unmarshal(raw, &data); err != nil {
		return
	}
	if data.Ips != nil {
		p.ips = data.Ips
	}
}

func (p *PrefStore) get(ip string) bool {
	if ip == "" {
		return false
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.ips[ip]
}

// set 记录/撤销某个 IP 的新版偏好，返回是否发生变化。
func (p *PrefStore) set(ip string, preferNew bool) bool {
	if ip == "" {
		return false
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	old := p.ips[ip]
	if preferNew {
		p.ips[ip] = true
	} else {
		delete(p.ips, ip)
	}
	if old == preferNew {
		return false
	}
	p.saveLocked()
	return true
}

func (p *PrefStore) saveLocked() {
	data := struct {
		Ips map[string]bool `json:"ips"`
	}{Ips: p.ips}
	raw, err := json.Marshal(data)
	if err != nil {
		return
	}
	tmp := p.path + ".tmp"
	if err := os.WriteFile(tmp, raw, 0o600); err != nil {
		return
	}
	_ = os.Rename(tmp, p.path)
}
