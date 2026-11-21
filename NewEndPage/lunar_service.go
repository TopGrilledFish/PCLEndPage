package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// LunarService 负责农历月日与节气（节气走线上接口，带本地缓存）。
type LunarService struct {
	cfg  *Config
	uapi *UapiClient
	path string
	cal  *CalendarData

	mu     sync.Mutex
	loaded bool
	date   string
	term   string
}

type lunarCache struct {
	Date string `json:"date"`
	Term string `json:"term"`
}

func newLunarService(cfg *Config, u *UapiClient, dir string, cal *CalendarData) *LunarService {
	return &LunarService{cfg: cfg, uapi: u, path: filepath.Join(dir, "lunar.json"), cal: cal}
}

func (l *LunarService) loadCache() *lunarCache {
	raw, err := os.ReadFile(l.path)
	if err != nil {
		return nil
	}
	var c lunarCache
	if json.Unmarshal(raw, &c) != nil {
		return nil
	}
	return &c
}

func (l *LunarService) saveCache(c lunarCache) {
	raw, err := json.MarshalIndent(c, "", "  ")
	if err != nil {
		return
	}
	_ = os.WriteFile(l.path, raw, 0o644)
}

// termFor 当天节气名（如「大雪」）；网络失败时返回空串并记录当天缓存。
func (l *LunarService) termFor(today time.Time) string {
	dateStr := today.Format("2006-01-02")
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.loaded && l.date == dateStr {
		return l.term
	}
	if c := l.loadCache(); c != nil && c.Date == dateStr {
		l.loaded, l.date, l.term = true, dateStr, c.Term
		return c.Term
	}
	term := ""
	if l.cfg.LunarAPI != "" {
		if payload, status := l.uapi.getJSON(l.cfg.LunarAPI); payload != nil && status >= 200 && status < 300 {
			term, _ = payload["solar_term"].(string)
		}
	}
	l.saveCache(lunarCache{Date: dateStr, Term: term})
	l.loaded, l.date, l.term = true, dateStr, term
	return term
}

// info 返回农历月、日、是否闰月、节气。
func (l *LunarService) info(today time.Time) (month, day int, isLeap bool, term string, ok bool) {
	if !l.cfg.EnableLunar || len(l.cal.LunarInfo) < 201 {
		return 0, 0, false, "", false
	}
	month, day, isLeap = l.solarToLunar(today)
	if month == 0 || day == 0 {
		return 0, 0, false, "", false
	}
	return month, day, isLeap, l.termFor(today), true
}
