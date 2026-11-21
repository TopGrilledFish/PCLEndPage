package main

import (
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"
)

var leftoverRE = regexp.MustCompile(`__[A-Z][A-Z0-9_]*__`)

// newTestServer 构造一个不开任何外部接口（天气/一言/农历/地理）的 Server，
// 让渲染测试不依赖网络。
func newTestServer(t *testing.T) *Server {
	t.Helper()
	dir := t.TempDir()
	cfg := &Config{
		Host:                "127.0.0.1",
		Port:                8778,
		EnableWallpaper:     false,
		EnableSaying:        false,
		EnableLunar:         false,
		EnableWeather:       false,
		EnableGeo:           false,
		TrustProxyHeaders:   true,
		MaxRetries:          1,
		HTTPTimeout:         1,
		GeoTimeout:          1,
		GeoCacheHours:       24,
		WeatherCacheSeconds: 3600,
		LegacyBackend:       "127.0.0.1:8787",
		PrefFile:            "pref.json",
		VerifyFile:          filepath.Join(dir, "verify.json"),
		AdminToken:          "test",
		AdminSessionHours:   1,
		LogFile:             filepath.Join(dir, "server.log"),
		LegacyLogFile:       filepath.Join(dir, "legacy.log"),
		StoreFile:           filepath.Join(dir, "store.json"),
	}
	s, err := newServer(cfg, dir, "")
	if err != nil {
		t.Fatalf("newServer: %v", err)
	}
	return s
}

func TestReplacePlaceholders(t *testing.T) {
	tpl := `<root><!-- __BANNER__ --><a>__T_WELCOME__</a><b>__UNKNOWN__</b><!-- __EMPTY__ --></root>`
	got := replacePlaceholders(tpl, map[string]string{"BANNER": "<i>x</i>", "T_WELCOME": "欢迎"})
	want := `<root><i>x</i><a>欢迎</a><b>__UNKNOWN__</b></root>`
	if got != want {
		t.Fatalf("replacePlaceholders:\n got %q\nwant %q", got, want)
	}
}

func TestInjectNewSiteHintSkipsPropertyElements(t *testing.T) {
	body := []byte(`<StackPanel><StackPanel.Resources><Style x:Key="a"/></StackPanel.Resources><local:MyCard Title="t"/></StackPanel>`)
	got := string(injectNewSiteHint(body))
	if !strings.Contains(got, "</StackPanel.Resources>\n    "+newSiteHint) {
		t.Fatalf("横条没有插在属性元素之后：%s", got)
	}
	iRes := strings.Index(got, "</StackPanel.Resources>")
	iHint := strings.Index(got, newSiteHint)
	iCard := strings.Index(got, "<local:MyCard")
	if !(iRes < iHint && iHint < iCard) {
		t.Fatalf("顺序错误 res=%d hint=%d card=%d", iRes, iHint, iCard)
	}
}

func TestInjectNewSiteHintMyCardRoot(t *testing.T) {
	body := []byte(`<local:MyCard Title="访问浏览"><StackPanel Margin="25,40,23,20"><TextBlock Text="x"/></StackPanel></local:MyCard>`)
	got := string(injectNewSiteHint(body))
	if !strings.Contains(got, "<StackPanel Margin=\"25,40,23,20\">\n    "+newSiteHint) {
		t.Fatalf("MyCard 根时横条插错位置：%s", got)
	}
}

func TestDatedVersion(t *testing.T) {
	if got := datedVersion("2026", "11", "21"); got != "20261121" {
		t.Fatalf("datedVersion = %q, want 20261121", got)
	}
}

func TestStaticVersionStable(t *testing.T) {
	s := newTestServer(t)
	a := s.staticVersion()
	b := s.staticVersion()
	if a != b {
		t.Fatalf("同一模板版本号不稳定：%q vs %q", a, b)
	}
	s.template = s.template + " "
	if s.staticVersion() == a {
		t.Fatalf("模板变化后版本号未变")
	}
}

func TestBuildHomeNoLeftoverPlaceholders(t *testing.T) {
	s := newTestServer(t)
	body, err := s.buildHome("1.2.3.4", "https://end.g-fish.dpdns.org", false)
	if err != nil {
		t.Fatalf("buildHome: %v", err)
	}
	if m := leftoverRE.FindAllString(body, -1); len(m) > 0 {
		t.Fatalf("渲染结果残留占位符：%v", m)
	}
	if !strings.Contains(body, newSiteHint) {
		t.Fatalf("新版渲染结果缺少横条")
	}
	iRes := strings.Index(body, "</StackPanel.Resources>")
	iHint := strings.Index(body, newSiteHint)
	if iRes < 0 || iHint < 0 || iHint < iRes {
		t.Fatalf("横条位置错误：res=%d hint=%d", iRes, iHint)
	}
	if !strings.Contains(body, "Welcome") && !strings.Contains(body, "欢迎") {
		t.Fatalf("渲染结果缺少欢迎卡标题")
	}
}

func TestTOTPRoundTrip(t *testing.T) {
	secret := newTOTPSecret()
	now := time.Unix(1700000000, 0)
	code := totpCode(secret, now.Unix()/30)
	if len(code) != 6 {
		t.Fatalf("TOTP 位数 = %d", len(code))
	}
	if !verifyTOTP(secret, code, now) {
		t.Fatalf("verifyTOTP 拒绝了正确验证码")
	}
	if !verifyTOTP(secret, code, now.Add(30*time.Second)) {
		t.Fatalf("verifyTOTP 未接受 ±1 窗口")
	}
	if verifyTOTP(secret, code, now.Add(10*time.Minute)) {
		t.Fatalf("verifyTOTP 接受了过期验证码")
	}
}
