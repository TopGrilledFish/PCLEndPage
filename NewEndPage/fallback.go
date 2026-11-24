package main

import (
	"encoding/json"
	"io"
	"net/http"
	"os"
	"strings"
)

// 旧版刷新按钮的几何 Logo（PCL-CE 的 AXAML 转换会把它换成 lucide/refresh-cw）。
const refreshLogo = "M512 192a320 320 0 1 0 0 640 320 320 0 0 0 0-640z M480 288h64v208l144 88-32 56-176-104V288z"

// refreshButton 旧版同款「刷新页面」按钮。
func (s *Server) refreshButton() string {
	label := s.i18n.T("btn.refresh_page")
	if label == "" {
		label = "刷新页面"
	}
	return `<local:MyIconTextButton Margin="0,24,0,0" Height="40" HorizontalAlignment="Center" Text="` +
		escapeAttr(label) + `" LogoScale="0.9" ColorType="Highlight" Logo="` + refreshLogo +
		`" EventType="刷新页面" EventData="-" />`
}

// buildFallbackXAML 封禁/维护兜底页（对应旧版 xaml.build_fallback_xaml）。
func (s *Server) buildFallbackXAML(title, msg, eta, reason string, maintenance bool) string {
	if !maintenance {
		return "<StackPanel><local:MyCard Title=\"\" Margin=\"0,0,0,12\"><StackPanel Margin=\"30,40,30,32\">" +
			"<Grid Width=\"64\" Height=\"64\" HorizontalAlignment=\"Center\"><Ellipse Width=\"64\" Height=\"64\" Fill=\"#FFE5484D\"/>" +
			"<Path Data=\"M20,20 L44,44\" Stroke=\"#FFFFFFFF\" StrokeThickness=\"8\" StrokeStartLineCap=\"Round\" StrokeEndLineCap=\"Round\"/></Grid>" +
			"<TextBlock Text=\"" + escapeAttr(title) + "\" FontSize=\"20\" FontWeight=\"Bold\" Foreground=\"{DynamicResource ColorBrush1}\" TextAlignment=\"Center\" HorizontalAlignment=\"Center\" Margin=\"0,18,0,0\"/>" +
			"<TextBlock Text=\"" + escapeAttr(msg) + "\" FontSize=\"15\" Foreground=\"{DynamicResource ColorBrush3}\" TextAlignment=\"Center\" TextWrapping=\"Wrap\" MaxWidth=\"420\" HorizontalAlignment=\"Center\" LineHeight=\"24\" Margin=\"0,10,0,0\"/>" +
			s.refreshButton() +
			"</StackPanel></local:MyCard></StackPanel>"
	}

	spinner := "<Grid Width=\"64\" Height=\"64\" HorizontalAlignment=\"Center\">" +
		"<Ellipse Width=\"64\" Height=\"64\" Stroke=\"#22000000\" StrokeThickness=\"5\"/>" +
		"<Ellipse Width=\"64\" Height=\"64\" Stroke=\"#FF4C8DFF\" StrokeThickness=\"5\" StrokeDashArray=\"1.4,100\" StrokeDashCap=\"Round\" RenderTransformOrigin=\"0.5,0.5\">" +
		"<Ellipse.RenderTransform><RotateTransform x:Name=\"spin\" Angle=\"0\"/></Ellipse.RenderTransform>" +
		"<Ellipse.Triggers><EventTrigger RoutedEvent=\"Ellipse.Loaded\"><BeginStoryboard><Storyboard RepeatBehavior=\"Forever\">" +
		"<DoubleAnimation Storyboard.TargetName=\"spin\" Storyboard.TargetProperty=\"Angle\" From=\"0\" To=\"360\" Duration=\"0:0:1.1\"/>" +
		"</Storyboard></BeginStoryboard></EventTrigger></Ellipse.Triggers>" +
		"</Ellipse></Grid>"

	etaLine := ""
	if eta != "" && eta != "0" {
		t := strings.ReplaceAll(s.i18n.T("fallback.eta"), "{eta}", eta)
		etaLine = "<TextBlock Text=\"" + escapeAttr(t) + "\" FontSize=\"15\" Foreground=\"{DynamicResource ColorBrush3}\" TextAlignment=\"Center\" HorizontalAlignment=\"Center\" Margin=\"0,16,0,0\"/>"
	}
	reasonLine := ""
	if strings.TrimSpace(reason) != "" {
		t := strings.ReplaceAll(s.i18n.T("fallback.reason"), "{reason}", reason)
		reasonLine = "<TextBlock Text=\"" + escapeAttr(t) + "\" FontSize=\"12\" Foreground=\"{DynamicResource ColorBrush2}\" TextAlignment=\"Center\" TextWrapping=\"Wrap\" MaxWidth=\"440\" HorizontalAlignment=\"Center\" LineHeight=\"22\" Margin=\"0,8,0,0\"/>"
	}

	return "<StackPanel><local:MyCard Title=\"\" Margin=\"0,0,0,12\"><StackPanel Margin=\"30,40,30,32\">" + spinner +
		"<TextBlock Text=\"" + escapeAttr(title) + "\" FontSize=\"20\" FontWeight=\"Bold\" Foreground=\"{DynamicResource ColorBrush1}\" TextAlignment=\"Center\" HorizontalAlignment=\"Center\" Margin=\"0,20,0,0\"/>" +
		"<TextBlock Text=\"" + escapeAttr(s.i18n.T("fallback.status")) + "\" FontSize=\"15\" Foreground=\"{DynamicResource ColorBrush3}\" TextAlignment=\"Center\" TextWrapping=\"Wrap\" MaxWidth=\"440\" HorizontalAlignment=\"Center\" LineHeight=\"26\" Margin=\"0,18,0,0\"/>" +
		reasonLine + etaLine + s.refreshButton() +
		"<local:MyHint Theme=\"Yellow\" Margin=\"0,18,0,0\" Text=\"" + escapeAttr(msg) + "\" />" +
		"<local:MyHint Theme=\"Blue\" Margin=\"0,10,0,0\" Text=\"" + escapeAttr(s.i18n.T("fallback.issue")) + "\" />" +
		"</StackPanel></local:MyCard></StackPanel>"
}

// readStoreFile 读旧版 store.json（缺失/损坏时返回 nil）。
func readStoreFile(path string) map[string]any {
	if path == "" {
		return nil
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil
	}
	return m
}

// guardHome 拦截：被封禁的 IP 返回拒绝页，维护模式下非白名单返回维护页。
func (s *Server) guardHome(ip string) (string, bool) {
	store := readStoreFile(s.cfg.StoreFile)
	if store == nil {
		return "", false
	}
	if block, ok := store["block:list"].(map[string]any); ok && ip != "" {
		if _, hit := block[ip]; hit {
			return s.buildFallbackXAML(s.i18n.T("fallback.banned_title"), s.i18n.T("fallback.banned_msg"), "", "", false), true
		}
	}
	if live, _ := store["maint_mode_live"].(string); live == "1" {
		wl, _ := store["maint_whitelist"].(map[string]any)
		if _, ok := wl[ip]; !ok {
			eta, _ := store["maint_eta"].(string)
			reason, _ := store["maint_reason"].(string)
			return s.buildFallbackXAML(s.i18n.T("fallback.updating_title"), s.i18n.T("fallback.updating_msg"), eta, reason, true), true
		}
	}
	return "", false
}

// writeXAMLPage 写 XAML 响应（wantAXAML 时转 AXAML）。
func (s *Server) writeXAMLPage(w http.ResponseWriter, r *http.Request, body string, wantAXAML bool) {
	if wantAXAML {
		if ax := toAXAML(body); axamlOK(ax) {
			body = ax
			w.Header().Set("X-Axaml", "1")
		}
	}
	// 所有新版渲染的页面都带「是否新站」提示（主页/验证卡已自带，避免重复）。
	if !strings.Contains(body, "您现在访问的是") {
		if wantAXAML {
			body = string(injectCESiteHint([]byte(body)))
		} else {
			body = string(injectNewSiteHint([]byte(body)))
		}
	}
	w.Header().Set("Content-Type", xamlCT)
	w.Header().Set("Cache-Control", noStore)
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if r.Method == http.MethodHead {
		w.WriteHeader(http.StatusOK)
		return
	}
	io.WriteString(w, body)
}
