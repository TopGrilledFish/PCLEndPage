package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

// AI 是「AI 日志分析」的全部运行状态：额度、任务、分析记录、访客自带密钥。
type AI struct {
	srv     *Server
	cfg     *Config
	quota   *aiQuota
	jobsMu  sync.Mutex
	jobs    map[string]map[string]any
	logMu   sync.Mutex
	log     []map[string]any
	ownMu   sync.Mutex
	own     map[string]map[string]any
	dataDir string
}

func newAI(s *Server, dataDir string) *AI {
	a := &AI{
		srv:     s,
		cfg:     s.cfg,
		quota:   newAIQuota(filepath.Join(dataDir, "ai_usage.json")),
		jobs:    map[string]map[string]any{},
		log:     []map[string]any{},
		own:     map[string]map[string]any{},
		dataDir: dataDir,
	}
	a.loadJobs()
	a.loadLog()
	return a
}

func (a *AI) jobsPath() string { return filepath.Join(a.dataDir, "ai_jobs.json") }
func (a *AI) logPath() string  { return filepath.Join(a.dataDir, "ai_log.json") }

// ---------- 任务 ----------

func (a *AI) loadJobs() {
	raw, err := os.ReadFile(a.jobsPath())
	if err != nil {
		return
	}
	var data map[string]map[string]any
	if json.Unmarshal(raw, &data) != nil {
		return
	}
	now := time.Now().Unix()
	for ip, job := range data {
		if job == nil {
			continue
		}
		if state, _ := job["state"].(string); state == "running" {
			// 进程重启：上一轮跑到一半的任务已经不可能有结果了。
			job["state"] = "error"
			lang, _ := job["lang"].(string)
			if lang == "" {
				lang = "zh-hans"
			}
			job["text"] = a.srv.i18n.Tlang("ai.err.interrupted", lang)
		}
		if ts, ok := toFloat(job["ts"]); ok && int64(ts) < now-aiJobTTL {
			continue
		}
		a.jobs[ip] = job
	}
}

func (a *AI) saveJobsLocked() {
	payload, err := json.Marshal(a.jobs)
	if err != nil {
		return
	}
	tmp := a.jobsPath() + ".tmp"
	if os.WriteFile(tmp, payload, 0o600) == nil {
		_ = os.Rename(tmp, a.jobsPath())
	}
}

func (a *AI) getJob(ip string) map[string]any {
	a.jobsMu.Lock()
	defer a.jobsMu.Unlock()
	job := a.jobs[ip]
	if job == nil {
		return nil
	}
	if ts, ok := toFloat(job["ts"]); ok && int64(ts) < time.Now().Unix()-aiJobTTL {
		delete(a.jobs, ip)
		a.saveJobsLocked()
		return nil
	}
	return job
}

func (a *AI) setJob(ip string, updates map[string]any) {
	a.jobsMu.Lock()
	defer a.jobsMu.Unlock()
	job := a.jobs[ip]
	if job == nil {
		job = map[string]any{}
		a.jobs[ip] = job
	}
	for key, value := range updates {
		job[key] = value
	}
	job["ts"] = float64(time.Now().Unix())
	a.saveJobsLocked()
}

func (a *AI) resetJobs(ip string) int {
	a.jobsMu.Lock()
	defer a.jobsMu.Unlock()
	if ip != "" {
		if _, ok := a.jobs[ip]; ok {
			delete(a.jobs, ip)
			a.saveJobsLocked()
			return 1
		}
		return 0
	}
	count := len(a.jobs)
	a.jobs = map[string]map[string]any{}
	a.saveJobsLocked()
	return count
}

// ---------- 分析记录 ----------

func (a *AI) loadLog() {
	raw, err := os.ReadFile(a.logPath())
	if err != nil {
		return
	}
	var wrapper struct {
		Records []map[string]any `json:"records"`
	}
	if json.Unmarshal(raw, &wrapper) == nil {
		a.log = wrapper.Records
	}
}

func (a *AI) saveLogLocked() {
	if len(a.log) > aiLogMax {
		a.log = a.log[len(a.log)-aiLogMax:]
	}
	payload, err := json.Marshal(map[string]any{"records": a.log})
	if err != nil {
		return
	}
	tmp := a.logPath() + ".tmp"
	if os.WriteFile(tmp, payload, 0o600) == nil {
		_ = os.Rename(tmp, a.logPath())
	}
}

func firstN(text string, n int) string {
	runes := []rune(text)
	if len(runes) <= n {
		return text
	}
	return string(runes[:n])
}

func (a *AI) recordAnalysis(ip, who, key, model, protocol, kind, input string, ok bool,
	output string, ms int, usage aiUsage, base string, cost int, peak bool, reasoning string) {
	record := map[string]any{
		"time":       time.Now().Format("2006-01-02 15:04:05"),
		"ip":         ip,
		"who":        who,
		"key":        firstN(key, 12) + "..." + key[len(key)-4:],
		"key_masked": a.maskKey(key),
		"model":      model,
		"protocol":   protocol,
		"base":       base,
		"kind":       kind,
		"input":      firstN(input, aiLogInputChars),
		"ok":         ok,
		"output":     firstN(output, aiLogTextChars),
		"ms":         ms,
		"tokens":     map[string]any{"in": usage.In, "out": usage.Out},
		"cost":       cost,
		"peak":       peak,
		"reasoning":  firstN(reasoning, aiLogReasoningChars),
	}
	// key 字段上面做了截断展示；真正要还原的是原始记录里的完整 key 掩码，
	// 这里覆盖成掩码形式，避免把明文密钥写进日志。
	record["key"] = a.maskKey(key)
	a.logMu.Lock()
	defer a.logMu.Unlock()
	a.log = append(a.log, record)
	if len(a.log) > aiLogMax {
		a.log = a.log[len(a.log)-aiLogMax:]
	}
	a.saveLogLocked()
}

func (a *AI) recentAnalyses(limit int) []map[string]any {
	a.logMu.Lock()
	defer a.logMu.Unlock()
	out := make([]map[string]any, 0, limit)
	for i := len(a.log) - 1; i >= 0 && len(out) < limit; i-- {
		out = append(out, a.log[i])
	}
	return out
}

func (a *AI) clearAnalyses() bool {
	a.logMu.Lock()
	defer a.logMu.Unlock()
	a.log = []map[string]any{}
	a.saveLogLocked()
	return true
}

// ---------- 访客自带密钥 ----------

func (a *AI) maskKey(key string) string {
	if len(key) <= 10 {
		return "***"
	}
	return key[:6] + "***" + key[len(key)-4:]
}

func (a *AI) ownKey(ip string) map[string]any {
	a.ownMu.Lock()
	defer a.ownMu.Unlock()
	return a.own[ip]
}

func (a *AI) setOwnKey(ip, raw string) bool {
	key := strings.TrimSpace(raw)
	if key == "" {
		return false
	}
	a.ownMu.Lock()
	defer a.ownMu.Unlock()
	conf := a.own[ip]
	if conf == nil {
		conf = map[string]any{}
	}
	conf["key"] = key
	a.own[ip] = conf
	return true
}

func (a *AI) clearOwnKey(ip string) bool {
	a.ownMu.Lock()
	defer a.ownMu.Unlock()
	if _, ok := a.own[ip]; !ok {
		return false
	}
	delete(a.own, ip)
	return true
}

func (a *AI) setOwnBase(ip, raw, protocol string) bool {
	base := strings.TrimSpace(strings.Split(raw, "|")[0])
	a.ownMu.Lock()
	defer a.ownMu.Unlock()
	conf := a.own[ip]
	if conf == nil {
		conf = map[string]any{}
	}
	if base != "" {
		conf["base"] = strings.TrimRight(base, "/")
	} else {
		delete(conf, "base")
	}
	conf["protocol"] = protocol
	a.own[ip] = conf
	return true
}

func (a *AI) setOwnModel(ip, raw string) bool {
	model := strings.TrimSpace(raw)
	a.ownMu.Lock()
	defer a.ownMu.Unlock()
	conf := a.own[ip]
	if conf == nil {
		conf = map[string]any{}
	}
	if model != "" {
		conf["model"] = model
	} else {
		delete(conf, "model")
	}
	a.own[ip] = conf
	return true
}

func (a *AI) resetKeys(ip string) int {
	a.ownMu.Lock()
	defer a.ownMu.Unlock()
	if ip != "" {
		if _, ok := a.own[ip]; ok {
			delete(a.own, ip)
			return 1
		}
		return 0
	}
	count := len(a.own)
	a.own = map[string]map[string]any{}
	return count
}

// resetAI 对应旧版 ai.reset(scope, ip)：quota 清额度、jobs 清任务、keys 清自带密钥。
func (a *AI) resetAI(scope, ip string) map[string]any {
	result := map[string]any{}
	if scope == "quota" || scope == "all" {
		result["quota"] = a.quota.reset("", ip)
	}
	if scope == "jobs" || scope == "all" {
		result["jobs"] = a.resetJobs(ip)
	}
	if scope == "keys" || scope == "all" {
		result["keys"] = a.resetKeys(ip)
	}
	return result
}

// ---------- 额度 ----------

func (a *AI) quotaState(ip, lang string) map[string]any {
	c := a.cfg
	limit := c.aiTokenLimitOrZero()
	bucket := aiTokenBucket(c)
	used, calls := a.quota.get(ip, bucket)
	gLimit := c.aiGlobalWeekLimitOrZero()
	gBucket := aiWeekBucket()
	gUsed, _ := a.quota.get(aiGlobalIP, gBucket)
	left := 0
	if limit > 0 {
		left = limit - used
		if left < 0 {
			left = 0
		}
	}
	percent := 0.0
	if limit > 0 {
		percent = float64(used) * 100 / float64(limit)
		if percent > 100 {
			percent = 100
		}
	}
	gLeft := 0
	gPercent := 0.0
	if gLimit > 0 {
		gLeft = gLimit - gUsed
		if gLeft < 0 {
			gLeft = 0
		}
		gPercent = float64(gUsed) * 100 / float64(gLimit)
		if gPercent > 100 {
			gPercent = 100
		}
	}
	return map[string]any{
		"bucket":         bucket,
		"limit":          limit,
		"used":           used,
		"calls":          calls,
		"left":           left,
		"unlimited":      limit <= 0,
		"percent":        percent,
		"reset_at":       aiBucketResetAt(c),
		"window":         c.aiWindowLabel(lang),
		"peak":           aiIsPeak(),
		"factor":         c.aiPeakFactorValue(),
		"global_bucket":  gBucket,
		"global_limit":   gLimit,
		"global_used":    gUsed,
		"global_unlimited": gLimit <= 0,
		"global_left":    gLeft,
		"global_percent": gPercent,
		"global_reset_at": aiWeekResetAt(),
	}
}

// ---------- 提交 ----------

func toFloat(v any) (float64, bool) {
	switch t := v.(type) {
	case float64:
		return t, true
	case int:
		return float64(t), true
	case json.Number:
		f, err := t.Float64()
		return f, err == nil
	case string:
		var f float64
		if _, err := fmt.Sscanf(t, "%f", &f); err == nil {
			return f, true
		}
	}
	return 0, false
}

func toInt(v any) int {
	f, ok := toFloat(v)
	if !ok {
		return 0
	}
	return int(f)
}

func copyMap(src map[string]any) map[string]any {
	out := map[string]any{}
	for key, value := range src {
		out[key] = value
	}
	return out
}

func anyList(v any) []map[string]any {
	out := []map[string]any{}
	switch list := v.(type) {
	case []map[string]any:
		out = append(out, list...)
	case []any:
		for _, item := range list {
			if m, ok := item.(map[string]any); ok {
				out = append(out, m)
			}
		}
	}
	return out
}

func toStringList(v any) []string {
	out := []string{}
	switch list := v.(type) {
	case []any:
		for _, item := range list {
			out = append(out, toString(item))
		}
	case []string:
		out = append(out, list...)
	}
	return out
}

func trimHistory(history []map[string]any) []map[string]any {
	kept := make([]map[string]any, 0, len(history))
	for _, message := range history {
		if toString(message["content"]) != "" {
			kept = append(kept, message)
		}
	}
	if len(kept) > aiHistoryMaxTurns*2 {
		kept = kept[len(kept)-aiHistoryMaxTurns*2:]
	}
	total := 0
	keptBack := make([]map[string]any, 0, len(kept))
	for i := len(kept) - 1; i >= 0; i-- {
		length := len([]rune(toString(kept[i]["content"])))
		if total+length > aiHistoryMaxChars && len(keptBack) > 0 {
			break
		}
		keptBack = append(keptBack, kept[i])
		total += length
	}
	out := make([]map[string]any, 0, len(keptBack))
	for i := len(keptBack) - 1; i >= 0; i-- {
		out = append(out, keptBack[i])
	}
	return out
}

func (a *AI) submit(ip, rawInput string, useOwn bool, lang string, follow bool) (bool, string) {
	if !a.cfg.EnableAI {
		return false, a.srv.i18n.Tlang("ai.err.disabled", lang)
	}
	job := a.getJob(ip)
	if job != nil {
		if state, _ := job["state"].(string); state == "running" {
			return false, a.srv.i18n.Tlang("ai.err.busy", lang)
		}
	}
	var kind, value, display string
	if follow {
		history := trimHistory(anyList(job["history"]))
		state, _ := job["state"].(string)
		if len(history) == 0 || state != "done" {
			return false, a.srv.i18n.Tlang("ai.err.no_follow", lang)
		}
		question := strings.TrimSpace(rawInput)
		if question == "" {
			return false, a.srv.i18n.Tlang("ai.err.follow_empty", lang)
		}
		kind = "follow"
		value = firstN(question, aiMaxInlineChars)
		display = a.srv.i18n.Tf("ai.follow_note", lang, "n", fmt.Sprint(len([]rune(value))))
	} else {
		kind, value = splitAIInput(rawInput)
		if kind == "empty" {
			return false, a.srv.i18n.Tlang("ai.err.empty_input", lang)
		}
		if kind == "url" {
			display = value
		} else {
			display = a.srv.i18n.Tf("ai.inline_note", lang, "n", fmt.Sprint(len([]rune(value))))
		}
	}

	who := "builtin"
	if useOwn {
		who = "own"
	}
	keyConf := map[string]any{}
	if useOwn {
		saved := a.ownKey(ip)
		if saved == nil {
			return false, a.srv.i18n.Tlang("ai.err.no_saved_key", lang)
		}
		keyConf = copyMap(saved)
		protocol := toString(keyConf["protocol"])
		if protocol == "" {
			protocol = "openai"
		}
		if toString(keyConf["base"]) == "" {
			keyConf["base"] = aiDefaultBase(protocol)
		}
	} else {
		keyConf["key"] = a.cfg.AIAPIKey
		if a.cfg.AIAPIKey == "" {
			return false, a.srv.i18n.Tlang("ai.err.no_site_key", lang)
		}
	}

	bucket := aiTokenBucket(a.cfg)
	reserved := 0
	counted := !useOwn && !a.cfg.aiFreeIP(ip)
	var remaining any = nil
	if counted {
		allowed, reason, take := a.quota.reserve(a.cfg, ip, bucket, a.cfg.AIReserveTokens,
			a.cfg.aiTokenLimitOrZero(), a.cfg.AIRateLimit, aiWeekBucket(), a.cfg.aiGlobalWeekLimitOrZero())
		if !allowed {
			return false, a.reserveMessage(ip, reason, lang)
		}
		reserved = take
		remaining = a.quotaState(ip, lang)["left"]
	}

	a.setJob(ip, map[string]any{
		"state":     "running",
		"text":      "",
		"query":     display,
		"who":       who,
		"lang":      lang,
		"remaining": remaining,
		"started":   float64(time.Now().Unix()),
		"reasoning": "",
		"cost":      0,
		"reserved":  reserved,
		"follow":    follow,
		"error":     "",
	})
	go a.worker(ip, kind, value, keyConf, useOwn, lang, reserved, follow, aiWeekBucket())
	if follow {
		return true, a.srv.i18n.Tlang("ai.follow_submitted", lang)
	}
	return true, a.srv.i18n.Tlang("ai.submitted", lang)
}

func (a *AI) reserveMessage(ip, reason, lang string) string {
	state := a.quotaState(ip, lang)
	switch reason {
	case "global":
		return a.srv.i18n.Tf("ai.err.global_out", lang,
			"limit", aiFormatTokens(toInt(state["global_limit"])),
			"at", aiWeekResetText(lang))
	case "calls":
		return a.srv.i18n.Tf("ai.err.calls_out", lang,
			"n", fmt.Sprint(a.cfg.AIRateLimit), "window", toString(state["window"]))
	}
	return a.srv.i18n.Tf("ai.err.quota_out", lang,
		"used", aiFormatTokens(toInt(state["used"])),
		"limit", aiFormatTokens(toInt(state["limit"])),
		"window", toString(state["window"]))
}

// ---------- 后台任务 ----------

func (a *AI) worker(ip, kind, value string, keyConf map[string]any, useOwn bool,
	lang string, reserved int, follow bool, globalBucket string) {
	who := "builtin"
	if useOwn {
		who = "own"
	}
	key := toString(keyConf["key"])
	if key == "" {
		key = a.cfg.AIAPIKey
	}
	model := toString(keyConf["model"])
	if model == "" {
		model = a.cfg.AIModel
	}
	protocol := strings.ToLower(toString(keyConf["protocol"]))
	if protocol == "" {
		protocol = "openai"
	}
	base := toString(keyConf["base"])
	if base == "" {
		base = a.cfg.AIAPIBase
	}
	base = strings.TrimRight(base, "/")

	note := func(inputText string, ok bool, output string, ms int, usage aiUsage, cost int, reasoning string) {
		a.recordAnalysis(ip, who, key, model, protocol, kind, inputText, ok, output, ms, usage, base, cost, aiIsPeak(), reasoning)
	}

	started := time.Now()
	job := a.getJob(ip)
	previous := []map[string]any{}
	if job != nil {
		previous = trimHistory(anyList(job["history"]))
	}
	var turns []map[string]any
	var inputText string
	if follow {
		turns = append(previous, map[string]any{"role": "user", "content": value})
		inputText = value
	} else {
		logText := value
		if kind == "url" {
			ok, text := a.fetchLog(value, lang)
			if !ok {
				note(value, false, text, 0, aiUsage{}, 0, "")
				a.fail(ip, useOwn, text, lang, reserved, globalBucket)
				return
			}
			logText = text
		}
		inputText = logText
		trimmed := a.smartTrimLog(logText, a.cfg.AIMaxLogChars, lang)
		turns = []map[string]any{{"role": "user", "content": a.srv.i18n.Tlang("ai.user_prefix", lang) + "\n\n" + trimmed}}
	}

	ok, result, usage := a.analyze(turns, keyConf, lang)
	ms := int(time.Since(started).Milliseconds())
	if !ok {
		note(inputText, false, result, ms, aiUsage{}, 0, "")
		a.fail(ip, useOwn, result, lang, reserved, globalBucket)
		return
	}
	cost := aiUsageTokens(usage, a.cfg)
	a.settle(ip, reserved, cost, useOwn, globalBucket)
	history := trimHistory(append(turns, map[string]any{"role": "assistant", "content": result}))
	note(inputText, true, result, usage.MS, usage, cost, usage.Reasoning)
	var remaining any = nil
	if !useOwn && !a.cfg.aiFreeIP(ip) {
		remaining = a.quotaState(ip, lang)["left"]
	}
	a.setJob(ip, map[string]any{
		"state":     "done",
		"text":      result,
		"error":     "",
		"history":   history,
		"reasoning": firstN(usage.Reasoning, a.cfg.AIReasoningMaxChars),
		"cost":      cost,
		"tokens":    map[string]any{"in": usage.In, "out": usage.Out},
		"remaining": remaining,
	})
}

func (a *AI) settle(ip string, reserved, actual int, useOwn bool, globalBucket string) {
	if useOwn || a.cfg.aiFreeIP(ip) {
		return
	}
	a.quota.settle(ip, aiTokenBucket(a.cfg), reserved, actual, globalBucket)
}

func (a *AI) fail(ip string, useOwn bool, message, lang string, reserved int, globalBucket string) {
	if !useOwn && a.cfg.AIRefundOnFailure && !a.cfg.aiFreeIP(ip) {
		a.quota.refund(ip, aiTokenBucket(a.cfg), reserved, globalBucket)
		message += "\n" + a.srv.i18n.Tlang("ai.refunded", lang)
	}
	a.setJob(ip, map[string]any{
		"state":     "error",
		"text":      message,
		"error":     message,
		"reasoning": "",
		"cost":      0,
	})
}

func randID() string {
	buf := make([]byte, 8)
	_, _ = rand.Read(buf)
	return hex.EncodeToString(buf)
}

func conditionalStr(ok bool, whenTrue, whenFalse string) string {
	if ok {
		return whenTrue
	}
	return whenFalse
}

func choose(ok bool, whenTrue, whenFalse string) string {
	return conditionalStr(ok, whenTrue, whenFalse)
}

var _ = sort.Ints
