package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"
)

// visitKeepDays 与旧版 store.STATS_KEEP_DAYS 一致。
const visitKeepDays = 7

// visitRecord 一次访问（字段对应旧版 visits 表）。
type visitRecord struct {
	IP       string  `json:"ip"`
	Country  string  `json:"country"`
	City     string  `json:"city"`
	Province string  `json:"province"`
	TS       float64 `json:"ts"` // 毫秒时间戳
}

type visitStats struct {
	path        string
	mu          sync.Mutex
	records     []visitRecord
	lastCleanup float64
}

func newVisitStats(path string) *visitStats {
	st := &visitStats{path: path}
	if raw, err := os.ReadFile(path); err == nil {
		var data struct {
			Visits []visitRecord `json:"visits"`
		}
		if json.Unmarshal(raw, &data) == nil {
			st.records = data.Visits
		}
	}
	return st
}

func (v *visitStats) saveLocked() {
	if v.path == "" {
		return
	}
	data, err := json.Marshal(struct {
		Visits []visitRecord `json:"visits"`
	}{v.records})
	if err != nil {
		return
	}
	_ = os.MkdirAll(filepath.Dir(v.path), 0o755)
	tmp := v.path + ".tmp"
	if os.WriteFile(tmp, data, 0o600) == nil {
		_ = os.Rename(tmp, v.path)
	}
}

// record 记一次访问；写失败只打日志，绝不影响主页返回（旧版同口径）。
func (v *visitStats) record(ip, country, city, province string) {
	if ip == "" {
		return
	}
	if country == "" {
		country = "XX"
	}
	now := float64(time.Now().UnixMilli())
	v.mu.Lock()
	v.records = append(v.records, visitRecord{IP: ip, Country: country, City: city, Province: province, TS: now})
	if now-v.lastCleanup >= 86400000 {
		cutoff := now - float64(visitKeepDays)*86400000
		kept := v.records[:0]
		for _, r := range v.records {
			if r.TS >= cutoff {
				kept = append(kept, r)
			}
		}
		v.records = kept
		v.lastCleanup = now
	}
	v.saveLocked()
	v.mu.Unlock()
}

type visitCount struct {
	Name  string
	Count int
}

type visitRegion struct {
	Code     string
	City     string
	Province string
	Count    int
}

type visitHost struct {
	IP    string
	Count int
	Last  float64
}

type visitTrend struct {
	Date  string
	Count int
}

type visitRecent struct {
	Code     string
	City     string
	Province string
	TS       float64
}

type visitSummary struct {
	Total, Today, Unique, Domestic, Foreign int
	Countries                               []visitCount
	Regions, RegionsCN, RegionsAbroad       []visitRegion
	Provinces                               []visitCount
	Hosts                                   []visitHost
	Recent                                  []visitRecent
	Trend                                   []visitTrend
}

// emptyVisitSummary 统计读不出来时的空壳（旧版 empty_summary）。
func emptyVisitSummary(days int) *visitSummary {
	s := &visitSummary{}
	for i := 0; i < days; i++ {
		s.Trend = append(s.Trend, visitTrend{})
	}
	return s
}

// summary 复刻旧版 store.Stats.summary：天数、北京时间切天、国内/国外口径都一致。
func (v *visitStats) summary(days int) *visitSummary {
	if days <= 0 {
		days = visitKeepDays
	}
	now := float64(time.Now().UnixMilli()) / 1000.0
	cutoff := (now - float64(days)*86400) * 1000
	beijing := now + 8*3600
	todayStart := (beijing - modf(beijing, 86400) - 8*3600) * 1000
	dayStart := beijing - modf(beijing, 86400) - float64(days-1)*86400
	trendCutoff := (dayStart - 8*3600) * 1000
	home := map[string]bool{"CN": true, "HK": true, "MO": true, "TW": true}

	v.mu.Lock()
	records := make([]visitRecord, len(v.records))
	copy(records, v.records)
	v.mu.Unlock()

	s := &visitSummary{}
	countries := map[string]int{}
	regions := map[string]*visitRegion{}
	hosts := map[string]*visitHost{}
	provinces := map[string]int{}
	trendCounts := map[int64]int{}

	var recent []visitRecord

	for _, r := range records {
		if r.TS < cutoff {
			continue
		}
		code := r.Country
		if code == "" {
			code = "XX"
		}
		s.Total++
		if r.TS >= todayStart {
			s.Today++
		}
		if home[code] {
			s.Domestic++
		} else {
			s.Foreign++
		}
		countries[code]++
		key := code + "\x00" + r.City + "\x00" + r.Province
		if item, ok := regions[key]; ok {
			item.Count++
		} else {
			regions[key] = &visitRegion{Code: code, City: r.City, Province: r.Province, Count: 1}
		}
		if item, ok := hosts[r.IP]; ok {
			item.Count++
			if r.TS > item.Last {
				item.Last = r.TS
			}
		} else {
			hosts[r.IP] = &visitHost{IP: r.IP, Count: 1, Last: r.TS}
		}
		if r.Province != "" && home[code] {
			provinces[r.Province]++
		}
		if r.TS >= trendCutoff {
			day := int64((r.TS + 28800000) / 86400000)
			trendCounts[day]++
		}
		recent = append(recent, r)
	}
	uniques := map[string]bool{}
	for _, r := range records {
		if r.TS >= cutoff && !isLocalAddr(r.IP) {
			uniques[r.IP] = true
		}
	}
	s.Unique = len(uniques)

	for name, n := range countries {
		s.Countries = append(s.Countries, visitCount{Name: name, Count: n})
	}
	sort.Slice(s.Countries, func(i, j int) bool {
		if s.Countries[i].Count != s.Countries[j].Count {
			return s.Countries[i].Count > s.Countries[j].Count
		}
		return s.Countries[i].Name < s.Countries[j].Name
	})
	s.Countries = limitCounts(s.Countries, 12)

	regionRows := make([]visitRegion, 0, len(regions))
	for _, item := range regions {
		regionRows = append(regionRows, *item)
	}
	sort.Slice(regionRows, func(i, j int) bool {
		if regionRows[i].Count != regionRows[j].Count {
			return regionRows[i].Count > regionRows[j].Count
		}
		a := regionRows[i].Code + regionRows[i].Province + regionRows[i].City
		b := regionRows[j].Code + regionRows[j].Province + regionRows[j].City
		return a < b
	})
	regionRows = limitRegions(regionRows, 60)
	s.Regions = limitRegions(regionRows, 15)
	for _, item := range regionRows {
		if home[item.Code] {
			s.RegionsCN = append(s.RegionsCN, item)
		} else {
			s.RegionsAbroad = append(s.RegionsAbroad, item)
		}
	}
	s.RegionsCN = limitRegions(s.RegionsCN, 15)
	s.RegionsAbroad = limitRegions(s.RegionsAbroad, 15)

	for name, n := range provinces {
		s.Provinces = append(s.Provinces, visitCount{Name: name, Count: n})
	}
	sort.Slice(s.Provinces, func(i, j int) bool {
		if s.Provinces[i].Count != s.Provinces[j].Count {
			return s.Provinces[i].Count > s.Provinces[j].Count
		}
		return s.Provinces[i].Name < s.Provinces[j].Name
	})

	hostRows := make([]visitHost, 0, len(hosts))
	for _, item := range hosts {
		hostRows = append(hostRows, *item)
	}
	sort.Slice(hostRows, func(i, j int) bool {
		if hostRows[i].Count != hostRows[j].Count {
			return hostRows[i].Count > hostRows[j].Count
		}
		return hostRows[i].Last > hostRows[j].Last
	})
	s.Hosts = limitHosts(hostRows, 12)

	sort.Slice(recent, func(i, j int) bool { return recent[i].TS > recent[j].TS })
	if len(recent) > 20 {
		recent = recent[:20]
	}
	for _, r := range recent {
		s.Recent = append(s.Recent, visitRecent{Code: r.Country, City: r.City, Province: r.Province, TS: r.TS})
	}

	for i := 0; i < days; i++ {
		stamp := dayStart + float64(i)*86400
		date := time.Unix(int64(stamp), 0).UTC().Format("2006-01-02")
		s.Trend = append(s.Trend, visitTrend{Date: date, Count: trendCounts[int64(stamp/86400)]})
	}
	return s
}

func modf(v, d float64) float64 {
	m := v - float64(int64(v/d))*d
	if m < 0 {
		m += d
	}
	return m
}

func limitCounts(rows []visitCount, n int) []visitCount {
	if len(rows) > n {
		return rows[:n]
	}
	return rows
}

func limitRegions(rows []visitRegion, n int) []visitRegion {
	if len(rows) > n {
		return rows[:n]
	}
	return rows
}

func limitHosts(rows []visitHost, n int) []visitHost {
	if len(rows) > n {
		return rows[:n]
	}
	return rows
}
