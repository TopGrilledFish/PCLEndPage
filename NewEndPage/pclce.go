package main

// PCL-CE 客户端的识别辅助。
//
// PCL-CE 的主页请求 UA 里通常带 ``PCLCE/``，但经过 CDN/代理后不保证一定
// 还在；而 PCL-CE 拉主页前会先请求 ``.xaml.ini`` 版本号，PCL2 请求的是
// ``.xaml.version``。因此收到 ``.ini`` 时把这个 IP 记下来，随后它请求主页
// 就给 AXAML 版本。

import (
	"sync"
	"time"
)

// PCLCEStore 记录最近请求过 .xaml.ini 的 IP（带过期时间）。
type PCLCEStore struct {
	mu  sync.Mutex
	ttl time.Duration
	ips map[string]time.Time
}

func newPCLCEStore(ttl time.Duration) *PCLCEStore {
	return &PCLCEStore{ttl: ttl, ips: make(map[string]time.Time)}
}

func (p *PCLCEStore) mark(ip string) {
	if ip == "" {
		return
	}
	now := time.Now()
	p.mu.Lock()
	if len(p.ips) > 4096 {
		for key, exp := range p.ips {
			if exp.Before(now) {
				delete(p.ips, key)
			}
		}
	}
	p.ips[ip] = now.Add(p.ttl)
	p.mu.Unlock()
}

func (p *PCLCEStore) seen(ip string) bool {
	if ip == "" {
		return false
	}
	now := time.Now()
	p.mu.Lock()
	exp, ok := p.ips[ip]
	if ok && exp.After(now) {
		p.mu.Unlock()
		return true
	}
	delete(p.ips, ip)
	p.mu.Unlock()
	return false
}
