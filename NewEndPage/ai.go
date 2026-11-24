package main

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	aiGlobalIP          = "__global__"
	aiWeekSeconds       = 7 * 86400
	aiBeijingOffset     = 8 * 3600
	aiJobTTL            = 7 * 86400
	aiLogMax            = 300
	aiLogInputChars     = 4000
	aiLogTextChars      = 6000
	aiLogReasoningChars = 2400
	aiHistoryMaxTurns   = 8
	aiHistoryMaxChars   = 12000
	aiMaxInlineChars    = 40000
	aiProbeTimeout      = 10 * time.Second
)

// aiWindowSeconds 返回 token 额度窗口秒数（0 或负数按 5 小时，最少 600 秒）。
func (c *Config) aiWindowSeconds() int {
	hours := c.AITokenWindowHours
	if hours <= 0 {
		hours = 5
	}
	sec := int(hours * 3600)
	if sec < 600 {
		sec = 600
	}
	return sec
}

func (c *Config) aiTokenLimitOrZero() int {
	if c.AITokenLimit < 0 {
		return 0
	}
	return c.AITokenLimit
}

func (c *Config) aiGlobalWeekLimitOrZero() int {
	if c.AIGlobalWeekTokens <= 0 {
		return 0
	}
	return c.AIGlobalWeekTokens
}

func (c *Config) aiPeakFactorValue() float64 {
	if c.AIPeakMultiplier < 1 {
		return 1
	}
	return c.AIPeakMultiplier
}

func (c *Config) aiWindowLabel(lang string) string {
	hours := float64(c.aiWindowSeconds()) / 3600
	number := ""
	if math.Abs(hours-math.Round(hours)) < 0.01 {
		number = strconv.Itoa(int(math.Round(hours)))
	} else {
		number = strconv.FormatFloat(math.Round(hours*10)/10, 'f', 1, 64)
	}
	if strings.HasPrefix(strings.ToLower(lang), "zh") {
		return number + " 小时"
	}
	if math.Abs(hours-1) < 0.01 {
		return number + " hour"
	}
	return number + " hours"
}

func (c *Config) aiFreeIP(ip string) bool {
	for _, item := range c.AIFreeIPs {
		if strings.TrimSpace(item) == ip {
			return true
		}
	}
	return false
}

func aiWindowSlot(c *Config, moment float64) (day int, index int, seconds int) {
	seconds = c.aiWindowSeconds()
	local := moment + aiBeijingOffset
	day = int(local / 86400)
	index = int((local - float64(day)*86400) / float64(seconds))
	return
}

func aiTokenBucket(c *Config, now ...float64) string {
	moment := time.Now().Unix()
	if len(now) > 0 {
		moment = int64(now[0])
	}
	day, index, _ := aiWindowSlot(c, float64(moment))
	return strconv.Itoa(day) + "-" + strconv.Itoa(index)
}

func aiBucketResetAt(c *Config, now ...float64) float64 {
	moment := float64(time.Now().Unix())
	if len(now) > 0 {
		moment = now[0]
	}
	day, index, seconds := aiWindowSlot(c, moment)
	end := float64(day*86400 + (index+1)*seconds)
	if dayEnd := float64((day + 1) * 86400); end > dayEnd {
		end = dayEnd
	}
	return end - aiBeijingOffset
}

func aiWeekBucket(now ...float64) string {
	moment := float64(time.Now().Unix())
	if len(now) > 0 {
		moment = now[0]
	}
	return "W" + strconv.FormatInt(int64((moment+aiBeijingOffset)/aiWeekSeconds), 10)
}

func aiWeekResetAt(now ...float64) float64 {
	moment := float64(time.Now().Unix())
	if len(now) > 0 {
		moment = now[0]
	}
	local := moment + aiBeijingOffset
	index := int64(local / aiWeekSeconds)
	return float64((index+1)*aiWeekSeconds) - aiBeijingOffset
}

func aiIsPeak(now ...float64) bool {
	moment := float64(time.Now().Unix())
	if len(now) > 0 {
		moment = now[0]
	}
	local := time.Unix(int64(moment), 0).UTC().Add(aiBeijingOffset * time.Second)
	minutes := local.Hour()*60 + local.Minute()
	return !(minutes >= 30 && minutes < 510)
}

func aiPeakFactorAt(c *Config, now ...float64) float64 {
	if aiIsPeak(now...) {
		return c.aiPeakFactorValue()
	}
	return 1
}

// aiUsage 就是 Python 里的 {"in","out","ms","reasoning"}。
type aiUsage struct {
	In        int    `json:"in"`
	Out       int    `json:"out"`
	MS        int    `json:"ms"`
	Reasoning string `json:"reasoning"`
}

func aiUsageTokens(usage aiUsage, c *Config, now ...float64) int {
	total := usage.In + usage.Out
	return int(math.RoundToEven(float64(total) * aiPeakFactorAt(c, now...)))
}

func aiFormatTokens(count int) string {
	if count < 0 {
		count = 0
	}
	if count >= 10000 {
		return strconv.Itoa(int(math.RoundToEven(float64(count)/1000))) + "k"
	}
	if count >= 1000 {
		v := float64(count) / 1000
		r := math.RoundToEven(v*10) / 10
		return strconv.FormatFloat(r, 'f', 1, 64) + "k"
	}
	return strconv.Itoa(count)
}

// ============ 配额存储（Python 用 stats.db 的 ai_usage 表，这里用 JSON） ============

type aiQuotaRow struct {
	Used  int   `json:"used"`
	Calls int   `json:"calls"`
	TS    int64 `json:"ts"`
}

type aiQuota struct {
	path string
	mu   sync.Mutex
	rows map[string]*aiQuotaRow
}

func newAIQuota(path string) *aiQuota {
	q := &aiQuota{path: path, rows: map[string]*aiQuotaRow{}}
	q.load()
	return q
}

func aiQuotaKey(ip, bucket string) string { return ip + "|" + bucket }

func (q *aiQuota) load() {
	q.mu.Lock()
	defer q.mu.Unlock()
	raw, err := os.ReadFile(q.path)
	if err != nil {
		return
	}
	var data map[string]*aiQuotaRow
	if json.Unmarshal(raw, &data) == nil && data != nil {
		q.rows = data
	}
}

func (q *aiQuota) saveLocked() {
	tmp := q.path + ".tmp"
	if err := os.MkdirAll(filepath.Dir(q.path), 0o755); err != nil {
		return
	}
	raw, err := json.Marshal(q.rows)
	if err != nil {
		return
	}
	if err := os.WriteFile(tmp, raw, 0o600); err == nil {
		_ = os.Rename(tmp, q.path)
	}
}

func (q *aiQuota) get(ip, bucket string) (used, calls int) {
	q.mu.Lock()
	defer q.mu.Unlock()
	if row, ok := q.rows[aiQuotaKey(ip, bucket)]; ok {
		return row.Used, row.Calls
	}
	return 0, 0
}

func (q *aiQuota) addLocked(ip, bucket string, used float64, calls int) {
	key := aiQuotaKey(ip, bucket)
	row := q.rows[key]
	if row == nil {
		row = &aiQuotaRow{}
		q.rows[key] = row
	}
	row.Used = int(math.Max(0, float64(row.Used)+used))
	row.Calls = int(math.Max(0, float64(row.Calls)+float64(calls)))
	row.TS = time.Now().Unix()
}

func (q *aiQuota) reserve(c *Config, ip, bucket string, amount, limit, maxCalls int, globalBucket string, globalLimit int) (bool, string, int) {
	if amount < 1 {
		amount = 1
	}
	q.mu.Lock()
	defer q.mu.Unlock()
	if globalBucket != "" && globalLimit > 0 {
		g := q.rows[aiQuotaKey(aiGlobalIP, globalBucket)]
		gUsed := 0
		if g != nil {
			gUsed = g.Used
		}
		if gUsed >= globalLimit {
			q.saveLocked()
			return false, "global", 0
		}
		gTake := amount
		if rest := globalLimit - gUsed; gTake > rest {
			gTake = rest
		}
		if gTake < 1 {
			gTake = 1
		}
		q.addLocked(aiGlobalIP, globalBucket, float64(gTake), 1)
	}
	used, calls := 0, 0
	if row := q.rows[aiQuotaKey(ip, bucket)]; row != nil {
		used, calls = row.Used, row.Calls
	}
	if maxCalls > 0 && calls >= maxCalls {
		q.saveLocked()
		return false, "calls", 0
	}
	if limit > 0 && used >= limit {
		q.saveLocked()
		return false, "token", 0
	}
	take := amount
	if limit > 0 && used+amount > limit {
		take = limit - used
	}
	if take < 1 {
		take = 1
	}
	q.addLocked(ip, bucket, float64(take), 1)
	q.saveLocked()
	return true, "", take
}

func (q *aiQuota) settle(ip, bucket string, reserved, actual int, globalBucket string) {
	if actual == reserved {
		return
	}
	delta := actual - reserved
	q.mu.Lock()
	defer q.mu.Unlock()
	q.addLocked(ip, bucket, float64(delta), 0)
	if globalBucket != "" {
		q.addLocked(aiGlobalIP, globalBucket, float64(delta), 0)
	}
	q.saveLocked()
}

func (q *aiQuota) refund(ip, bucket string, amount int, globalBucket string) {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.addLocked(ip, bucket, -float64(amount), -1)
	if globalBucket != "" {
		q.addLocked(aiGlobalIP, globalBucket, -float64(amount), 0)
	}
	q.saveLocked()
}

func (q *aiQuota) rowsFor(bucket string) []map[string]any {
	q.mu.Lock()
	defer q.mu.Unlock()
	out := []map[string]any{}
	suffix := "|" + bucket
	for key, row := range q.rows {
		if !strings.HasSuffix(key, suffix) {
			continue
		}
		ip := strings.TrimSuffix(key, suffix)
		out = append(out, map[string]any{"ip": ip, "used": row.Used, "calls": row.Calls, "ts": row.TS})
	}
	return out
}

func (q *aiQuota) reset(bucket, ip string) int {
	q.mu.Lock()
	defer q.mu.Unlock()
	removed := 0
	for key := range q.rows {
		match := true
		if bucket != "" && !strings.HasSuffix(key, "|"+bucket) {
			match = false
		}
		if ip != "" && !strings.HasPrefix(key, ip+"|") {
			match = false
		}
		if match {
			delete(q.rows, key)
			removed++
		}
	}
	q.saveLocked()
	return removed
}

func aiResetText(c *Config, lang string) string {
	moment := time.Unix(int64(aiBucketResetAt(c)), 0).In(time.FixedZone("CST", aiBeijingOffset))
	return moment.Format("15:04")
}

func aiWeekResetText(lang string) string {
	moment := time.Unix(int64(aiWeekResetAt()), 0).In(time.FixedZone("CST", aiBeijingOffset))
	if strings.HasPrefix(strings.ToLower(lang), "zh") {
		return fmt.Sprintf("%02d月%02d日 %02d:%02d", int(moment.Month()), moment.Day(), moment.Hour(), moment.Minute())
	}
	return moment.Format("Jan 02 15:04")
}
