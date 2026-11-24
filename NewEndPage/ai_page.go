package main

import (
	"fmt"
	"strings"
)

const (
	iconAI    = "M512 96 L608 416 L928 512 L608 608 L512 928 L416 608 L96 512 L416 416 Z"
	iconSave  = "M480 96 H544 V480 H768 L512 832 L256 480 H480 Z"
	iconTrash = "M765.5 191.9 639.6 191.9c0-35.3-28.6-64-64-64L447.7 128c-35.3 0-64 28.6-64 64L257.9 191.9c-36.5 0-66 29.6-66 66l0 59.9c0 36.5 29.6 66 66 66l-2 0 0 445.7c0 36.5 29.6 66 66 66l61.9 0 64 0 127.9 0 64 0 61.9 0c36.5 0 66-29.6 66-66L767.5 383.8l-2 0c36.5 0 66-29.6 66-66l0-59.9C831.5 221.5 802 191.9 765.5 191.9zM703.6 803.4c-0.1 3.1-1.7 27.8-28 28.1l-36 0-64 0L447.7 831.5 383.8 831.5l-35.9 0c-28-0.3-28-28.5-28-28.5l-0.1 0L319.8 383.8l383.8 0L703.6 803.4zM735.6 319.9 287.8 319.9c-17.7 0-32-14.3-32-32 0-17.7 14.3-32 32-32l159.9 0 127.9 0 159.9 0c17.7 0 32 14.3 32 32C767.5 305.5 753.2 319.9 735.6 319.9z"
)

func calcGrid2(left, right, margin string) string {
	return `<Grid Margin="` + margin + `"><Grid.ColumnDefinitions><ColumnDefinition Width="1*" /><ColumnDefinition Width="1*" /></Grid.ColumnDefinitions>` +
		left + right + `</Grid>`
}

func calcDivider(margin string) string {
	return `<Border Height="1" Margin="` + margin + `"><Border.Background><LinearGradientBrush StartPoint="0,0" EndPoint="1,0">` +
		`<GradientStop Color="#00000000" Offset="0" /><GradientStop Color="#33808080" Offset="0.5" /><GradientStop Color="#00000000" Offset="1" />` +
		`</LinearGradientBrush></Border.Background></Border>`
}

func (a *AI) statusLine(ip, lang string) string {
	own := a.ownKey(ip)
	if own != nil {
		return a.srv.i18n.Tf("ai.status_own", lang, "desc", a.describeOwnKey(ip, lang))
	}
	if a.cfg.aiFreeIP(ip) {
		return a.srv.i18n.Tf("ai.status_free", lang)
	}
	state := a.quotaState(ip, lang)
	if toBool(state["unlimited"]) {
		return a.srv.i18n.Tf("ai.status_unlimited", lang)
	}
	left := toInt(state["left"])
	if left <= 0 {
		return a.srv.i18n.Tf("ai.status_out", lang, "window", toString(state["window"]), "at", aiResetText(a.cfg, lang))
	}
	return a.srv.i18n.Tf("ai.status_left", lang, "left", aiFormatTokens(left), "window", toString(state["window"]), "at", aiResetText(a.cfg, lang))
}

func toBool(v any) bool {
	b, _ := v.(bool)
	return b
}

func (a *AI) resultBlock(job map[string]any, lang string) string {
	if job == nil {
		return ""
	}
	state := toString(job["state"])
	if state == "running" {
		return `<local:MyHint Theme="Blue" Margin="0,14,0,0" Text="` + calcAttr(a.srv.i18n.Tf("ai.running", lang, "query", toString(job["query"]))) + `" />`
	}
	if state != "done" && state != "error" {
		return ""
	}
	done := state == "done"
	who := toString(job["who"])
	footer := ""
	if who == "own" || who == "私人密钥" {
		footer = a.srv.i18n.Tf("ai.footer_own", lang)
	} else {
		footer = a.srv.i18n.Tf("ai.footer", lang, "name", a.cfg.AIDisplayName)
	}
	if !done {
		return `<StackPanel Margin="0,14,0,0">` +
			`<TextBlock Text="` + escapeAttr(a.srv.i18n.Tf("ai.result_fail", lang)) + `" FontSize="14" FontWeight="Bold" Foreground="{DynamicResource ColorBrush1}" Margin="0,0,0,8" />` +
			`<Border Background="{DynamicResource ColorBrush7}" CornerRadius="8" Padding="16,14"><TextBlock Text="` + calcAttr(toString(job["text"])) + `" FontSize="13" LineHeight="24" TextWrapping="Wrap" Foreground="{DynamicResource ColorBrush1}" /></Border>` +
			`<TextBlock Text="` + calcAttr(footer) + `" FontSize="11" Margin="0,10,0,0" HorizontalAlignment="Right" Foreground="{DynamicResource ColorBrush3}" /></StackPanel>`
	}
	var parts []string
	parts = append(parts, `<StackPanel Margin="0,14,0,0">`)
	parts = append(parts, `<TextBlock Text="`+escapeAttr(a.srv.i18n.Tf("ai.result_ok", lang))+`" FontSize="14" FontWeight="Bold" Foreground="{DynamicResource ColorBrush1}" Margin="0,0,0,8" />`)
	history := anyList(job["history"])
	if len(history) > 0 {
		for _, message := range history {
			role := toString(message["role"])
			content := toString(message["content"])
			if content == "" {
				continue
			}
			if role == "assistant" {
				parts = append(parts, `<Border Background="{DynamicResource ColorBrush7}" CornerRadius="8" Padding="16,14" Margin="0,0,0,8"><TextBlock Text="`+calcAttr(content)+`" FontSize="13" LineHeight="24" TextWrapping="Wrap" Foreground="{DynamicResource ColorBrush1}" /></Border>`)
			} else {
				asked := content
				if len([]rune(asked)) > 400 {
					asked = string([]rune(asked)[:400])
				}
				parts = append(parts, `<TextBlock Text="`+calcAttr(a.srv.i18n.Tf("ai.you_asked", lang, "q", asked))+`" FontSize="11" LineHeight="18" TextWrapping="Wrap" Margin="0,0,0,6" Foreground="{DynamicResource ColorBrush3}" />`)
			}
		}
	} else {
		parts = append(parts, `<Border Background="{DynamicResource ColorBrush7}" CornerRadius="8" Padding="16,14"><TextBlock Text="`+calcAttr(toString(job["text"]))+`" FontSize="13" LineHeight="24" TextWrapping="Wrap" Foreground="{DynamicResource ColorBrush1}" /></Border>`)
	}
	reasoning := toString(job["reasoning"])
	if reasoning != "" {
		parts = append(parts, `<local:MyCard Title="`+escapeAttr(a.srv.i18n.Tf("ai.reasoning_title", lang))+`" CanSwap="True" Margin="0,10,0,0"><StackPanel Margin="18,26,16,14"><TextBlock Text="`+
			calcAttr(reasoning)+`" FontSize="11" LineHeight="18" TextWrapping="Wrap" Foreground="{DynamicResource ColorBrush2}" Opacity="0.85" /></StackPanel></local:MyCard>`)
	}
	cost := toInt(job["cost"])
	if cost > 0 {
		parts = append(parts, `<TextBlock Text="`+calcAttr(a.srv.i18n.Tf("ai.cost_line", lang, "n", aiFormatTokens(cost)))+`" FontSize="11" Margin="0,8,0,0" Foreground="{DynamicResource ColorBrush3}" />`)
	}
	parts = append(parts, `<TextBlock Text="`+calcAttr(footer)+`" FontSize="11" Margin="0,8,0,0" HorizontalAlignment="Right" Foreground="{DynamicResource ColorBrush3}" /></StackPanel>`)
	return strings.Join(parts, "")
}

func (a *AI) globalLine(ip, lang string) string {
	state := a.quotaState(ip, lang)
	if toBool(state["global_unlimited"]) {
		return ""
	}
	text := a.srv.i18n.Tf("ai.quota_global", lang,
		"used", aiFormatTokens(toInt(state["global_used"])),
		"limit", aiFormatTokens(toInt(state["global_limit"])),
		"at", aiWeekResetText(lang))
	return `<TextBlock Text="` + calcAttr(text) + `" FontSize="10" LineHeight="16" TextWrapping="Wrap" Margin="0,6,0,0" Foreground="{DynamicResource ColorBrush3}" />`
}

func (a *AI) quotaBar(ip, lang string) string {
	state := a.quotaState(ip, lang)
	if toBool(state["unlimited"]) {
		return a.srv.calcNote(a.srv.i18n.Tf("ai.quota_unlimited", lang, "window", toString(state["window"])), "0,8,0,0") + a.globalLine(ip, lang)
	}
	percent := toFloatValue(state["percent"])
	fill := 100 - int(roundHalfAway(percent))
	if fill < 0 {
		fill = 0
	}
	if fill > 100 {
		fill = 100
	}
	leftCols := 100 - fill
	if leftCols < 0 {
		leftCols = 0
	}
	rightCols := 1
	if leftCols < 100 {
		rightCols = fill
		if rightCols < 1 {
			rightCols = 1
		}
	}
	if percent < 1 && toInt(state["used"]) > 0 && leftCols < 1 {
		leftCols = 1
	}
	bar := `<Grid Margin="0,6,0,0"><Grid.ColumnDefinitions><ColumnDefinition Width="` + fmt.Sprint(leftCols) +
		`*" /><ColumnDefinition Width="` + fmt.Sprint(rightCols) + `*" /></Grid.ColumnDefinitions>` +
		`<Border Grid.Column="0" Height="7" CornerRadius="3.5" Background="{DynamicResource ColorBrush1}" Opacity="0.62" />` +
		`<Border Grid.Column="1" Height="7" CornerRadius="3.5" Background="{DynamicResource ColorBrush7}" /></Grid>`
	label := a.srv.i18n.Tf("ai.quota_line", lang,
		"used", aiFormatTokens(toInt(state["used"])),
		"limit", aiFormatTokens(toInt(state["limit"])),
		"percent", fmt.Sprint(int(roundHalfAway(percent))),
		"window", toString(state["window"]),
		"at", aiResetText(a.cfg, lang))
	return `<TextBlock Text="` + calcAttr(label) + `" FontSize="11" LineHeight="18" TextWrapping="Wrap" Margin="0,10,0,0" Foreground="{DynamicResource ColorBrush1}" />` + bar + a.globalLine(ip, lang)
}


func toFloatValue(v any) float64 {
	f, _ := toFloat(v)
	return f
}

func (a *AI) followBlock(base string, job map[string]any, lang string) string {
	return `<local:MyCard Title="` + escapeAttr(a.srv.i18n.Tf("ai.follow_title", lang)) + `" CanSwap="True" Margin="0,14,0,0">` +
		`<StackPanel Margin="18,26,16,14">` +
		`<TextBlock Text="` + escapeAttr(a.srv.i18n.Tf("ai.follow_hint", lang)) + `" FontSize="11" LineHeight="18" TextWrapping="Wrap" Margin="0,0,0,8" Foreground="{DynamicResource ColorBrush3}" />` +
		a.srv.calcInputRow("aifollowinput", a.srv.i18n.Tf("ai.follow_placeholder", lang), 38, "0,8,0,0", 430) +
		a.srv.calcHelpButton(a.srv.i18n.Tf("ai.follow_send", lang), iconAI, calcBindTo("aifollowinput", base, "/ai_follow.json"), 40, -1, "0,8,0,0", "Highlight") +
		`</StackPanel></local:MyCard>`
}

func (a *AI) buildPage(ip, base, lang string) string {
	if !a.cfg.EnableAI {
		return a.buildPopup(a.srv.i18n.Tf("ai.title", lang), a.srv.i18n.Tf("ai.err.page_disabled", lang), "Yellow", "", false, lang)
	}
	job := a.getJob(ip)
	var b strings.Builder
	b.WriteString(`<local:MyCard Title="` + escapeAttr(a.srv.i18n.Tf("ai.title", lang)) + `" CanSwap="False"><StackPanel Margin="25,40,23,20">`)
	b.WriteString(`<TextBlock Text="` + escapeAttr(a.srv.i18n.Tf("ai.headline", lang)) + `" FontSize="24" FontWeight="Bold" Foreground="#FF000000" HorizontalAlignment="Center" />`)
	b.WriteString(`<TextBlock TextWrapping="Wrap" FontSize="12" LineHeight="20" Margin="0,10,0,0" Foreground="{DynamicResource ColorBrush3}" Text="` + escapeAttr(a.srv.i18n.Tf("ai.intro", lang)) + `" />`)
	b.WriteString(`<local:MyTextButton Margin="0,4,0,0" HorizontalAlignment="Left" Text="` + escapeAttr(a.srv.i18n.Tf("ai.upload", lang)) + `" ToolTip="` + escapeAttr(a.srv.i18n.Tf("ai.upload_tip", lang)) + `" EventType="打开网页" EventData="https://mclo.gs/" />`)
	b.WriteString(a.srv.calcInputRow("ailoginput", a.srv.i18n.Tf("ai.log_hint", lang), 40, "0,14,0,0", 430))
	b.WriteString(calcGrid2(
		a.srv.calcHelpButton(a.srv.i18n.Tf("ai.builtin_key", lang), iconAI, calcBindTo("ailoginput", base, "/ai.json"), 46, 0, "0,0,5,0", "Highlight"),
		a.srv.calcHelpButton(a.srv.i18n.Tf("ai.own_key", lang), iconKey, base+"/ai_own_page.json", 46, 1, "5,0,0,0", "Highlight"),
		"0,10,0,0"))
	b.WriteString(a.srv.calcNote(a.srv.i18n.Tf("ai.limit", lang,
		"limit", aiFormatTokens(a.cfg.aiTokenLimitOrZero()),
		"window", a.cfg.aiWindowLabel(lang),
		"factor", fmt.Sprint(int(a.cfg.aiPeakFactorValue()))), "0,8,0,0"))
	b.WriteString(calcDivider("0,20,0,12"))
	b.WriteString(`<TextBlock Text="` + calcAttr(a.statusLine(ip, lang)) + `" FontSize="12" FontWeight="Bold" Foreground="{DynamicResource ColorBrush1}" />`)
	b.WriteString(a.quotaBar(ip, lang))
	b.WriteString(a.resultBlock(job, lang))
	if job != nil && toString(job["state"]) == "done" {
		b.WriteString(a.followBlock(base, job, lang))
	}
	b.WriteString(a.srv.calcNavRow(base+"/ai_page.json", "", "", lang))
	b.WriteString(`</StackPanel></local:MyCard>`)
	return b.String()
}

func (a *AI) buildOwnPage(ip, base, lang string) string {
	if !a.cfg.EnableAI {
		return a.buildPopup(a.srv.i18n.Tf("ai.title", lang), a.srv.i18n.Tf("ai.err.page_disabled", lang), "Yellow", "", false, lang)
	}
	job := a.getJob(ip)
	own := a.ownKey(ip)
	protocol := "openai"
	savedBase := ""
	savedModel := ""
	if own != nil {
		if p := toString(own["protocol"]); p != "" {
			protocol = p
		}
		savedBase = toString(own["base"])
		savedModel = toString(own["model"])
	}
	keyHint := ""
	if own != nil {
		keyHint = a.srv.i18n.Tf("ai.key_saved", lang, "key", a.maskKey(toString(own["key"])))
	} else {
		keyHint = a.srv.i18n.Tf("ai.key_hint", lang)
	}
	baseHint := ""
	if savedBase != "" {
		baseHint = a.srv.i18n.Tf("ai.base_saved", lang, "base", savedBase)
	} else {
		baseHint = a.srv.i18n.Tf("ai.base_hint", lang)
	}
	modelHint := ""
	if savedModel != "" {
		modelHint = a.srv.i18n.Tf("ai.model_saved", lang, "model", savedModel)
	} else {
		modelHint = a.srv.i18n.Tf("ai.model_hint", lang, "model", aiProtocols[protocol][1])
	}
	var b strings.Builder
	b.WriteString(`<local:MyCard Title="` + escapeAttr(a.srv.i18n.Tf("ai.own_key", lang)) + `" CanSwap="False"><StackPanel Margin="25,40,23,20">`)
	b.WriteString(`<TextBlock Text="` + escapeAttr(a.srv.i18n.Tf("ai.own_headline", lang)) + `" FontSize="24" FontWeight="Bold" Foreground="#FF000000" HorizontalAlignment="Center" />`)
	b.WriteString(a.srv.calcHeading(a.srv.i18n.Tf("ai.log_link", lang), "0,18,0,0"))
	b.WriteString(a.srv.calcInputRow("ailoginput", a.srv.i18n.Tf("ai.log_hint", lang), 38, "0,8,0,0", 430))
	b.WriteString(a.srv.calcHeading(a.srv.i18n.Tf("ai.api_key", lang), "0,16,0,0"))
	b.WriteString(a.srv.calcInputRow("aikeyinput", keyHint, 38, "0,8,0,0", 430))
	b.WriteString(calcGrid2(
		a.srv.calcHelpButton(a.srv.i18n.Tf("ai.save_key", lang), iconSave, calcBindTo("aikeyinput", base, "/ai_key.json"), 38, 0, "0,0,5,0", "Highlight"),
		a.srv.calcHelpButton(a.srv.i18n.Tf("ai.clear", lang), iconTrash, base+"/ai_clear.json", 38, 1, "5,0,0,0", ""),
		"0,8,0,0"))
	b.WriteString(a.srv.calcHeading(a.srv.i18n.Tf("ai.base_title", lang), "0,16,0,0"))
	b.WriteString(a.srv.calcInputRow("aibaseinput", baseHint, 38, "0,8,0,0", 430))
	b.WriteString(a.srv.calcHelpButton(a.srv.i18n.Tf("ai.protocol_auto", lang), iconSave, calcBindTo("aibaseinput", base, "/ai_base_auto.json"), 38, -1, "0,8,0,0", "Highlight"))
	b.WriteString(a.srv.calcHeading(a.srv.i18n.Tf("ai.model_title", lang), "0,16,0,0"))
	b.WriteString(a.srv.calcInputRow("aimodelinput", modelHint, 38, "0,8,0,0", 430))
	b.WriteString(a.srv.calcHelpButton(a.srv.i18n.Tf("ai.save_model", lang), iconSave, calcBindTo("aimodelinput", base, "/ai_model.json"), 38, -1, "0,8,0,0", "Highlight"))
	b.WriteString(calcDivider("0,22,0,12"))
	b.WriteString(a.srv.calcHelpButton(a.srv.i18n.Tf("ai.start", lang), iconAI, calcBindTo("ailoginput", base, "/ai_own.json"), 46, -1, "", "Highlight"))
	b.WriteString(`<TextBlock Text="` + calcAttr(a.statusLine(ip, lang)) + `" FontSize="12" FontWeight="Bold" Margin="0,12,0,0" Foreground="{DynamicResource ColorBrush1}" />`)
	b.WriteString(a.resultBlock(job, lang))
	b.WriteString(a.srv.calcNavRow(base+"/ai_own_page.json", a.srv.i18n.Tf("ai.back_to_analysis", lang), base+"/ai_page.json", lang))
	b.WriteString(`</StackPanel></local:MyCard>`)
	return b.String()
}

func (a *AI) buildPopup(title, message, theme, back string, home bool, lang string) string {
	var cells []string
	if home {
		cells = append(cells, `<local:MyButton Height="35" Width="110" Text="`+escapeAttr(a.srv.i18n.Tf("btn.back_home", lang))+
			`" EventType="切换页面" EventData="Launch" />`)
	}
	if back != "" {
		cells = append(cells, `<local:MyButton Height="35" Width="110" Text="`+escapeAttr(a.srv.i18n.Tf("btn.continue", lang))+
			`" ColorType="Highlight" EventType="打开帮助" EventData="`+escapeURL(back)+`" />`)
	} else {
		cells = append(cells, `<local:MyButton Height="35" Width="110" Text="`+escapeAttr(a.srv.i18n.Tf("btn.ok", lang))+
			`" ColorType="Highlight" EventType="刷新主页" EventData="-" />`)
	}
	rows := make([]string, 0, len(cells))
	for i, cell := range cells {
		if i == 0 {
			rows = append(rows, cell)
		} else {
			rows = append(rows, `<StackPanel Margin="8,0,0,0">`+cell+`</StackPanel>`)
		}
	}
	return `<local:MyCard Title="` + escapeAttr(title) + `" CanSwap="False" Width="460" HorizontalAlignment="Center" VerticalAlignment="Center">` +
		`<StackPanel Margin="25,40,25,20"><local:MyHint Theme="` + theme + `" Text="` + calcAttr(message) + `" />` +
		`<StackPanel Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,24,0,0">` + strings.Join(rows, "") + `</StackPanel></StackPanel></local:MyCard>`
}

func (a *AI) pictureJSON(title, desc string) string {
	return `{"Title": ` + jsonString(title) + `, "Description": ` + jsonString(desc) + `}`
}

func (a *AI) describeOwnKey(ip, lang string) string {
	conf := a.ownKey(ip)
	if conf == nil {
		return a.srv.i18n.Tf("ai.own_unset", lang)
	}
	protocol := toString(conf["protocol"])
	if protocol == "" {
		protocol = "openai"
	}
	base := toString(conf["base"])
	if base == "" {
		base = aiDefaultBase(protocol)
	}
	model := toString(conf["model"])
	if model == "" {
		model = aiProtocols[protocol][1]
	}
	return a.protocolName(protocol, lang) + " · " + base + " · " + model
}

func (a *AI) protocolName(protocol, lang string) string {
	key := "ai.protocol." + protocol
	if text := a.srv.i18n.Tlang(key, lang); text != "" && text != key {
		return text
	}
	if v, ok := aiProtocols[protocol]; ok {
		return v[0]
	}
	return protocol
}
