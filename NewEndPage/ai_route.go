package main

import (
	"net/http"
	"net/url"
	"strings"
)

var aiMeta = map[string][2]string{
	"/ai_page.json":           {"ai.title", "ai.meta.page"},
	"/ai_own_page.json":       {"ai.own_key", "ai.meta.own_page"},
	"/ai.json":                {"ai.title", "ai.meta.builtin"},
	"/ai_own.json":            {"ai.title", "ai.meta.own"},
	"/ai_follow.json":         {"ai.follow_title", "ai.meta.follow"},
	"/ai_key.json":            {"ai.meta.save_key_title", "ai.meta.save_key"},
	"/ai_base_openai.json":    {"ai.meta.base_openai_title", "ai.meta.base_openai"},
	"/ai_base_auto.json":      {"ai.meta.base_auto_title", "ai.meta.base_auto"},
	"/ai_base_anthropic.json": {"ai.meta.base_anthropic_title", "ai.meta.base_anthropic"},
	"/ai_model.json":          {"ai.save_model", "ai.meta.model"},
	"/ai_clear.json":          {"ai.meta.clear_title", "ai.meta.clear"},
}

var aiDo = map[string]string{
	"/ai_page.xaml":         "page",
	"/ai_own_page.xaml":     "own_page",
	"/ai.xaml":              "builtin",
	"/ai_own.xaml":          "own",
	"/ai_follow.xaml":       "follow",
	"/ai_key.xaml":          "savekey",
	"/ai_base_auto.xaml":    "base_auto",
	"/ai_base_openai.xaml":  "base_openai",
	"/ai_base_anthropic.xaml": "base_anthropic",
	"/ai_model.xaml":        "savemodel",
	"/ai_clear.xaml":        "clearkey",
}

// aiQuery 宽松解析查询串：PCL 会把输入框原文直接拼进 URL，可能带非法百分号转义。
func aiQuery(raw string) map[string]string {
	out := map[string]string{}
	if raw == "" {
		return out
	}
	if values, err := url.ParseQuery(raw); err == nil {
		for key, list := range values {
			if len(list) > 0 {
				out[key] = list[0]
			}
		}
		return out
	}
	for _, pair := range strings.Split(raw, "&") {
		key, value, found := strings.Cut(pair, "=")
		if !found {
			continue
		}
		if decoded, err := url.QueryUnescape(value); err == nil {
			out[key] = decoded
		} else {
			out[key] = value
		}
	}
	return out
}

func (a *AI) handleAI(w http.ResponseWriter, r *http.Request, ip string) bool {
	path := r.URL.Path
	meta, isMeta := aiMeta[path]
	action, isDo := aiDo[path]
	if !isMeta && !isDo {
		return false
	}
	lang := a.srv.profiles.langFor(ip)
	if isMeta {
		body := a.pictureJSON(a.srv.i18n.Tlang(meta[0], lang), a.srv.i18n.Tlang(meta[1], lang))
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.Header().Set("Cache-Control", "no-store")
		_, _ = w.Write([]byte(body))
		return true
	}

	params := aiQuery(r.URL.RawQuery)
	base := a.srv.verify.base(requestOrigin(r))
	pageURL := base + "/ai_page.json"
	ownURL := base + "/ai_own_page.json"
	axaml := isPCLCE(r.UserAgent())
	styled := func(body string) string { return applyPalette(body, a.srv.profiles.paletteFor(ip)) }
	render := func(body string) { a.srv.writeXAMLPage(w, r, styled(body), axaml) }

	switch action {
	case "page":
		render(a.buildPage(ip, base, lang))
		return true
	case "own_page":
		render(a.buildOwnPage(ip, base, lang))
		return true
	case "savekey":
		if a.setOwnKey(ip, params["q"]) {
			render(a.buildPopup(a.srv.i18n.Tf("ai.popup.key_saved", lang), a.srv.i18n.Tf("ai.popup.key_saved_msg", lang), "Blue", ownURL, true, lang))
		} else {
			render(a.buildPopup(a.srv.i18n.Tf("ai.popup.key_empty", lang), a.srv.i18n.Tf("ai.popup.key_empty_msg", lang), "Yellow", ownURL, true, lang))
		}
		return true
	case "base_auto":
		if a.ownKey(ip) == nil {
			render(a.buildPopup(a.srv.i18n.Tf("ai.popup.need_key", lang), a.srv.i18n.Tf("ai.popup.need_key_base", lang), "Yellow", ownURL, true, lang))
			return true
		}
		raw := strings.TrimSpace(strings.Split(params["q"], "|")[0])
		if raw == "" {
			a.setOwnBase(ip, "", "openai")
			render(a.buildPopup(
				a.srv.i18n.Tf("ai.popup.base_saved", lang, "protocol", a.protocolName("openai", lang)),
				a.srv.i18n.Tf("ai.popup.now", lang, "desc", a.describeOwnKey(ip, lang)),
				"Blue", ownURL, true, lang))
			return true
		}
		own := a.ownKey(ip)
		key := ""
		if own != nil {
			key = toString(own["key"])
		}
		protocol, err := a.detectProtocol(raw, key, lang)
		if protocol == "" {
			render(a.buildPopup(a.srv.i18n.Tf("ai.popup.detect_failed", lang), err, "Yellow", ownURL, true, lang))
			return true
		}
		a.setOwnBase(ip, raw, protocol)
		render(a.buildPopup(
			a.srv.i18n.Tf("ai.popup.detect_ok", lang, "protocol", a.protocolName(protocol, lang)),
			a.srv.i18n.Tf("ai.popup.now", lang, "desc", a.describeOwnKey(ip, lang)),
			"Blue", ownURL, true, lang))
		return true
	case "base_openai", "base_anthropic":
		protocol := "openai"
		if action == "base_anthropic" {
			protocol = "anthropic"
		}
		if a.ownKey(ip) == nil {
			render(a.buildPopup(a.srv.i18n.Tf("ai.popup.need_key", lang), a.srv.i18n.Tf("ai.popup.need_key_base", lang), "Yellow", ownURL, true, lang))
			return true
		}
		a.setOwnBase(ip, params["q"], protocol)
		render(a.buildPopup(
			a.srv.i18n.Tf("ai.popup.base_saved", lang, "protocol", a.protocolName(protocol, lang)),
			a.srv.i18n.Tf("ai.popup.now", lang, "desc", a.describeOwnKey(ip, lang)),
			"Blue", ownURL, true, lang))
		return true
	case "savemodel":
		if a.ownKey(ip) == nil {
			render(a.buildPopup(a.srv.i18n.Tf("ai.popup.need_key", lang), a.srv.i18n.Tf("ai.popup.need_key_model", lang), "Yellow", ownURL, true, lang))
			return true
		}
		a.setOwnModel(ip, params["q"])
		render(a.buildPopup(
			a.srv.i18n.Tf("ai.popup.model_saved", lang),
			a.srv.i18n.Tf("ai.popup.model_saved_msg", lang, "desc", a.describeOwnKey(ip, lang)),
			"Blue", ownURL, true, lang))
		return true
	case "clearkey":
		had := a.clearOwnKey(ip)
		titleKey, msgKey := "ai.popup.nothing", "ai.popup.nothing_msg"
		if had {
			titleKey, msgKey = "ai.popup.cleared", "ai.popup.cleared_msg"
		}
		render(a.buildPopup(a.srv.i18n.Tf(titleKey, lang), a.srv.i18n.Tf(msgKey, lang), "Blue", ownURL, true, lang))
		return true
	}

	useOwn := action == "own"
	follow := action == "follow"
	ok, message := a.submit(ip, params["q"], useOwn, lang, follow)
	theme := "Blue"
	back := pageURL
	if !ok {
		theme = "Yellow"
	}
	if useOwn {
		back = ownURL
	}
	titleKey := "ai.popup.started"
	if !ok {
		titleKey = "ai.popup.not_started"
	}
	render(a.buildPopup(a.srv.i18n.Tf(titleKey, lang), message, theme, back, true, lang))
	return true
}
