package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strings"
	"time"
)

var (
	aiURLRe    = regexp.MustCompile(`(?i)^https?://\S+$`)
	aiMclogsRe = regexp.MustCompile(`(?i)^https?://(?:www\.)?mclo\.gs/([A-Za-z0-9]+)`)
)

var aiProtocols = map[string][2]string{
	"openai":    {"OpenAI 兼容", "gpt-4o-mini"},
	"anthropic": {"Anthropic", "claude-sonnet-5"},
}

func aiDefaultBase(protocol string) string {
	if protocol == "anthropic" {
		return "https://api.anthropic.com"
	}
	return "https://api.openai.com/v1"
}

func aiEndpoint(base, protocol string) string {
	base = strings.TrimRight(base, "/")
	if protocol == "anthropic" {
		if strings.HasSuffix(base, "/v1") {
			return base + "/messages"
		}
		return base + "/v1/messages"
	}
	if strings.HasSuffix(base, "/v1") {
		return base + "/chat/completions"
	}
	return base + "/v1/chat/completions"
}

func splitAIInput(raw string) (string, string) {
	text := strings.TrimSpace(raw)
	if text == "" {
		return "empty", ""
	}
	if aiURLRe.MatchString(text) {
		return "url", text
	}
	if len(text) > aiMaxInlineChars {
		text = text[:aiMaxInlineChars]
	}
	return "text", text
}

func normalizeLogURL(url string) string {
	if m := aiMclogsRe.FindStringSubmatch(url); m != nil {
		return "https://api.mclo.gs/1/raw/" + m[1]
	}
	return url
}

func (a *AI) httpClient(timeout time.Duration) *http.Client {
	return &http.Client{Timeout: timeout}
}

func (a *AI) fetchLog(url, lang string) (bool, string) {
	client := a.httpClient(time.Duration(a.cfg.AITimeout) * time.Second)
	if a.cfg.AITimeout <= 0 {
		client = a.httpClient(120 * time.Second)
	}
	req, err := http.NewRequest(http.MethodGet, normalizeLogURL(url), nil)
	if err != nil {
		return false, a.srv.i18n.Tf("ai.err.log_fetch", lang, "err", err.Error())
	}
	req.Header.Set("User-Agent", "PCL2/2.13.0")
	req.Header.Set("Accept", "text/plain, */*")
	resp, err := client.Do(req)
	if err != nil {
		return false, a.srv.i18n.Tf("ai.err.log_fetch", lang, "err", err.Error())
	}
	defer resp.Body.Close()
	if resp.StatusCode == 404 {
		return false, a.srv.i18n.Tf("ai.err.log_404", lang)
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return false, a.srv.i18n.Tf("ai.err.log_http", lang, "code", fmt.Sprint(resp.StatusCode))
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, int64(a.cfg.AIMaxLogChars*10)))
	if err != nil {
		return false, a.srv.i18n.Tf("ai.err.log_fetch", lang, "err", err.Error())
	}
	text := string(raw)
	if strings.TrimSpace(text) == "" {
		return false, a.srv.i18n.Tf("ai.err.log_empty", lang)
	}
	return true, text
}

func (a *AI) trimLog(text string, limit int, lang string) string {
	if len(text) <= limit {
		return text
	}
	head := limit * 25 / 100
	tail := limit - head
	return text[:head] + "\n\n" + a.srv.i18n.Tf("ai.trim_note", lang, "n", fmt.Sprint(len(text)-limit)) + "\n\n" + text[len(text)-tail:]
}

var aiImportantMarkers = []string{
	"ERROR", "FATAL", "Exception", "Caused by", "Stacktrace", "Suspected",
	"Mixin", "Crash", "Failed", "failed", "Could not", "could not",
	"---- Minecraft Crash Report ----", "Description:",
}

func (a *AI) smartTrimLog(text string, limit int, lang string) string {
	if len(text) <= limit {
		return text
	}
	lines := strings.Split(text, "\n")
	total := len(lines)
	if total == 0 {
		return text
	}
	important := make([]bool, total)
	for i, line := range lines {
		for _, marker := range aiImportantMarkers {
			if strings.Contains(line, marker) {
				for near := i - 2; near <= i+2; near++ {
					if near >= 0 && near < total {
						important[near] = true
					}
				}
				break
			}
		}
	}
	chosen := []int{}
	used := 0
	for i := 0; i < total; i++ {
		if !important[i] {
			continue
		}
		used += len(lines[i]) + 1
		chosen = append(chosen, i)
	}
	if used > limit {
		return a.trimLog(text, limit, lang)
	}
	taken := map[int]bool{}
	for _, i := range chosen {
		taken[i] = true
	}
	for i := total - 1; i >= 0; i-- {
		if taken[i] {
			continue
		}
		cost := len(lines[i]) + 1
		if used+cost > limit {
			break
		}
		used += cost
		chosen = append(chosen, i)
		taken[i] = true
	}
	sortInts(chosen)
	var out []string
	previous := -1
	for _, i := range chosen {
		if previous >= 0 && i-previous > 1 {
			out = append(out, a.srv.i18n.Tf("ai.trim_note_lines", lang, "n", fmt.Sprint(i-previous-1)))
		}
		out = append(out, lines[i])
		previous = i
	}
	return strings.Join(out, "\n")
}

func sortInts(v []int) {
	for i := 1; i < len(v); i++ {
		for j := i; j > 0 && v[j] < v[j-1]; j-- {
			v[j], v[j-1] = v[j-1], v[j]
		}
	}
}

func (a *AI) postJSON(url string, payload any, headers map[string]string, timeout time.Duration) (map[string]any, string) {
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, a.srv.i18n.Tf("ai.err.unreachable", "", "err", err.Error())
	}
	req, err := http.NewRequest(http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, a.srv.i18n.Tf("ai.err.unreachable", "", "err", err.Error())
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", "PCL2/2.13.0")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	client := &http.Client{Timeout: timeout}
	resp, err := client.Do(req)
	if err != nil {
		return nil, a.srv.i18n.Tf("ai.err.unreachable", "", "err", err.Error())
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		detail := strings.TrimSpace(string(raw))
		if len(detail) > 300 {
			detail = detail[:300]
		}
		known := map[int]string{401: "ai.err.key_invalid", 402: "ai.err.no_balance",
			403: "ai.err.forbidden", 429: "ai.err.rate_limit"}
		suffix := ""
		if detail != "" {
			suffix = "：" + detail
		}
		if key, ok := known[resp.StatusCode]; ok {
			return nil, a.srv.i18n.Tf(key, "") + a.srv.i18n.Tf("ai.err.http_code", "", "code", fmt.Sprint(resp.StatusCode)) + suffix
		}
		return nil, a.srv.i18n.Tf("ai.err.http", "", "code", fmt.Sprint(resp.StatusCode)) + suffix
	}
	var data map[string]any
	if err := json.Unmarshal(raw, &data); err != nil {
		return nil, a.srv.i18n.Tf("ai.err.unparsable", "", "body", firstN(string(raw), 280))
	}
	return data, ""
}

func (a *AI) detectProtocol(base, key, lang string) (string, string) {
	ok, errOpenAI := a.probe(base, key, "openai")
	if ok {
		return "openai", ""
	}
	ok, errAnthropic := a.probe(base, key, "anthropic")
	if ok {
		return "anthropic", ""
	}
	if errOpenAI == "" {
		errOpenAI = "?"
	}
	if errAnthropic == "" {
		errAnthropic = "?"
	}
	return "", a.srv.i18n.Tf("ai.err.detect_failed", lang, "openai", errOpenAI, "anthropic", errAnthropic)
}

func (a *AI) probe(base, key, protocol string) (bool, string) {
	model := aiProtocols[protocol][1]
	payload := map[string]any{
		"model":      model,
		"max_tokens": 1,
		"messages":   []map[string]any{{"role": "user", "content": "hi"}},
	}
	headers := map[string]string{}
	if protocol == "anthropic" {
		headers["x-api-key"] = key
		headers["anthropic-version"] = "2023-06-01"
	} else {
		headers["Authorization"] = "Bearer " + key
	}
	body, _ := json.Marshal(payload)
	req, err := http.NewRequest(http.MethodPost, aiEndpoint(base, protocol), bytes.NewReader(body))
	if err != nil {
		return false, firstN(err.Error(), 120)
	}
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	client := &http.Client{Timeout: aiProbeTimeout}
	resp, err := client.Do(req)
	if err != nil {
		return false, firstN(err.Error(), 120)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 2048))
	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		return true, ""
	}
	detail := strings.TrimSpace(string(raw))
	if len(detail) > 120 {
		detail = detail[:120]
	}
	msg := "HTTP " + fmt.Sprint(resp.StatusCode)
	if detail != "" {
		msg += " " + detail
	}
	return false, msg
}

func (a *AI) parseOpenAI(data map[string]any, lang string) (bool, string) {
	choices, _ := data["choices"].([]any)
	if len(choices) == 0 {
		return false, a.srv.i18n.Tf("ai.err.unparsable", lang, "body", firstN(jsonStringOf(data), 280))
	}
	choice, _ := choices[0].(map[string]any)
	message, _ := choice["message"].(map[string]any)
	text := strings.TrimSpace(toString(message["content"]))
	finish := toString(choice["finish_reason"])
	if text != "" {
		if finish == "length" {
			text += "\n\n" + a.srv.i18n.Tf("ai.err.cut_note", lang, "n", fmt.Sprint(a.cfg.AIMaxTokens))
		}
		return true, text
	}
	if finish == "length" {
		return false, a.srv.i18n.Tf("ai.err.too_long", lang, "n", fmt.Sprint(a.cfg.AIMaxTokens))
	}
	return false, a.srv.i18n.Tf("ai.err.empty_finish", lang, "reason", choose(finish != "", finish, a.srv.i18n.Tf("ai.none", lang)))
}

func jsonStringOf(data map[string]any) string {
	raw, err := json.Marshal(data)
	if err != nil {
		return ""
	}
	return string(raw)
}

func (a *AI) parseAnthropic(data map[string]any, lang string) (bool, string) {
	text := ""
	blocks, _ := data["content"].([]any)
	for _, item := range blocks {
		block, ok := item.(map[string]any)
		if ok && toString(block["type"]) == "text" {
			text += toString(block["text"])
		}
	}
	text = strings.TrimSpace(text)
	stop := toString(data["stop_reason"])
	if text != "" {
		if stop == "max_tokens" {
			text += "\n\n" + a.srv.i18n.Tf("ai.err.cut_note", lang, "n", fmt.Sprint(a.cfg.AIMaxTokens))
		}
		return true, text
	}
	if stop == "max_tokens" {
		return false, a.srv.i18n.Tf("ai.err.too_long", lang, "n", fmt.Sprint(a.cfg.AIMaxTokens))
	}
	return false, a.srv.i18n.Tf("ai.err.empty_stop", lang, "reason", choose(stop != "", stop, a.srv.i18n.Tf("ai.none", lang)))
}

func reasoningOf(data map[string]any) string {
	choices, _ := data["choices"].([]any)
	if len(choices) > 0 {
		if choice, ok := choices[0].(map[string]any); ok {
			if message, ok := choice["message"].(map[string]any); ok {
				for _, key := range []string{"reasoning_content", "reasoning"} {
					if v := strings.TrimSpace(toString(message[key])); v != "" {
						return v
					}
				}
			}
		}
	}
	if blocks, ok := data["content"].([]any); ok {
		parts := []string{}
		for _, item := range blocks {
			if block, ok := item.(map[string]any); ok && toString(block["type"]) == "thinking" {
				parts = append(parts, toString(block["thinking"]))
			}
		}
		if joined := strings.TrimSpace(strings.Join(parts, "")); joined != "" {
			return joined
		}
	}
	return ""
}

func (a *AI) analyze(messages []map[string]any, keyConf map[string]any, lang string) (bool, string, aiUsage) {
	key := toString(keyConf["key"])
	if key == "" {
		key = a.cfg.AIAPIKey
	}
	if key == "" {
		return false, a.srv.i18n.Tf("ai.err.no_key", lang), aiUsage{}
	}
	protocol := strings.ToLower(toString(keyConf["protocol"]))
	if protocol != "anthropic" {
		protocol = "openai"
	}
	base := toString(keyConf["base"])
	if base == "" {
		base = a.cfg.AIAPIBase
	}
	model := toString(keyConf["model"])
	if model == "" {
		model = a.cfg.AIModel
	}
	url := aiEndpoint(base, protocol)
	prompt := a.srv.i18n.Tf("ai.system_prompt", lang)
	turns := []map[string]any{}
	for _, m := range messages {
		if toString(m["content"]) != "" {
			turns = append(turns, m)
		}
	}
	var payload map[string]any
	headers := map[string]string{}
	parse := a.parseOpenAI
	if protocol == "anthropic" {
		payload = map[string]any{"model": model, "max_tokens": a.cfg.AIMaxTokens, "system": prompt, "messages": turns}
		headers["x-api-key"] = key
		headers["anthropic-version"] = "2023-06-01"
		parse = a.parseAnthropic
	} else {
		allTurns := append([]map[string]any{{"role": "system", "content": prompt}}, turns...)
		payload = map[string]any{"model": model, "max_tokens": a.cfg.AIMaxTokens, "temperature": 0.3, "stream": false, "messages": allTurns}
		effort := strings.ToLower(strings.TrimSpace(a.cfg.AIReasoningEffort))
		if effort == "low" || effort == "medium" || effort == "high" {
			payload["reasoning_effort"] = effort
		}
		headers["Authorization"] = "Bearer " + key
	}
	started := time.Now()
	data, errMsg := a.postJSON(url, payload, headers, time.Duration(a.cfg.AITimeout)*time.Second)
	if errMsg != "" {
		return false, errMsg, aiUsage{}
	}
	ok, result := parse(data, lang)
	if !ok {
		return false, result, aiUsage{}
	}
	usageRaw, _ := data["usage"].(map[string]any)
	in := toInt(usageRaw["prompt_tokens"])
	if in == 0 {
		in = toInt(usageRaw["input_tokens"])
	}
	out := toInt(usageRaw["completion_tokens"])
	if out == 0 {
		out = toInt(usageRaw["output_tokens"])
	}
	return true, result, aiUsage{In: in, Out: out, MS: int(time.Since(started).Milliseconds()), Reasoning: reasoningOf(data)}
}
