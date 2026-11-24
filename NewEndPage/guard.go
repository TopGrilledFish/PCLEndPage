package main

import (
	"net/http"
	"strings"
	"sync"
	"time"
)

// ceStore 记录最近访问过 /Custom-CE.xaml 的 IP，反代旧版页面时用它告诉旧版
// 输出 AXAML（旧版在 X-PCLCE: 1 时统一按 PCL-CE 处理）。只做 AXAML 适配，
// 不参与「新版/旧版」入口选择。
type ceStore struct {
	mu  sync.Mutex
	ips map[string]time.Time
}

const ceTTL = time.Hour

func newCEStore() *ceStore {
	return &ceStore{ips: map[string]time.Time{}}
}

func (c *ceStore) mark(ip string) {
	if ip == "" {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	now := time.Now()
	for k, t := range c.ips {
		if now.Sub(t) > ceTTL {
			delete(c.ips, k)
		}
	}
	c.ips[ip] = now
}

func (c *ceStore) seen(ip string) bool {
	if ip == "" {
		return false
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	t, ok := c.ips[ip]
	return ok && time.Since(t) <= ceTTL
}

// isTrustedClient 判断请求是否来自 PCL 启动器或真实浏览器（对应旧版
// is_trusted_client）：UA 带 PCL2 / Mozilla，或 Referer 是 PCL 的帮助页。
// 纯脚本、扫描器会被拒绝页挡下。
func isTrustedClient(r *http.Request) bool {
	ua := strings.ToLower(r.UserAgent())
	if strings.Contains(ua, "pcl2") || strings.Contains(ua, "mozilla") {
		return true
	}
	ref := strings.ToLower(r.Referer())
	return strings.Contains(ref, "pcl2.server") || strings.Contains(ref, "pcl2.open.server")
}
