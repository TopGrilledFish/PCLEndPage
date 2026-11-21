package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// SayingService 每日一言：API → 本地缓存 → 内置句子。
type SayingService struct {
	cfg    *Config
	uapi   *UapiClient
	path   string
	quotes []string
	mu     sync.Mutex
}

type sayingCache struct {
	Date   string `json:"date"`
	Text   string `json:"text"`
	Author string `json:"author"`
}

func newSayingService(cfg *Config, u *UapiClient, dir string, text *TextData) *SayingService {
	return &SayingService{cfg: cfg, uapi: u, path: filepath.Join(dir, "saying.json"), quotes: text.Quotes}
}

func (s *SayingService) load() *sayingCache {
	raw, err := os.ReadFile(s.path)
	if err != nil {
		return nil
	}
	var c sayingCache
	if json.Unmarshal(raw, &c) != nil {
		return nil
	}
	return &c
}

func (s *SayingService) save(c sayingCache) {
	raw, err := json.MarshalIndent(c, "", "  ")
	if err != nil {
		return
	}
	_ = os.WriteFile(s.path, raw, 0o644)
}

// quote 返回当日一言文本。
func (s *SayingService) quote(today time.Time) string {
	if !s.cfg.EnableSaying {
		return ""
	}
	dateStr := today.Format("2006-01-02")
	s.mu.Lock()
	defer s.mu.Unlock()
	cached := s.load()
	if cached != nil && cached.Date == dateStr && cached.Text != "" {
		return cached.Text
	}
	api := strings.TrimRight(s.cfg.SayingsAPI, "/")
	if api != "" {
		if payload, status := s.uapi.getJSON(api + "?mode=daily"); payload != nil && status >= 200 && status < 300 {
			if item, ok := payload["item"].(map[string]any); ok {
				if txt, ok := item["content"].(string); ok && txt != "" {
					author, _ := item["author"].(string)
					s.save(sayingCache{Date: dateStr, Text: txt, Author: author})
					return txt
				}
			}
			if txt, ok := payload["text"].(string); ok && txt != "" {
				s.save(sayingCache{Date: dateStr, Text: txt})
				return txt
			}
		}
	}
	if cached != nil && cached.Text != "" {
		return cached.Text
	}
	if len(s.quotes) > 0 {
		ordinal := int(today.Sub(time.Date(1, 1, 1, 0, 0, 0, 0, time.UTC)).Hours()/24) + 1
		idx := ordinal % len(s.quotes)
		if idx < 0 {
			idx += len(s.quotes)
		}
		return s.quotes[idx]
	}
	return ""
}
