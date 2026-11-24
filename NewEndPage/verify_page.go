package main

import (
	"bytes"
	"crypto/hmac"
	"html"
	"image"
	"image/color"
	"image/png"
	"math"
	"math/rand"
	"net/url"
	"strings"
	"time"
)

// ---------- 验证码图片 ----------

// 5×7 点阵字库，覆盖 ALPHABET 里的 31 个字符（与旧版 verify.py 一致）。
var verifyFont = map[byte]string{
	'2': ".####/#...#/....#/...#./..#../.#.../#####",
	'3': "#####/....#/...#./..##./....#/#...#/.###.",
	'4': "...#./..##./.#.#./#..#./#####/...#./...#.",
	'5': "#####/#..../####./....#/....#/#...#/.###.",
	'6': "..##./.#.../#..../####./#...#/#...#/.###.",
	'7': "#####/....#/...#./..#../.#.../.#.../.#...",
	'8': ".###./#...#/#...#/.###./#...#/#...#/.###.",
	'9': ".###./#...#/#...#/.####/....#/...#./.##..",
	'A': "..#../.#.#./#...#/#...#/#####/#...#/#...#",
	'B': "####./#...#/#...#/####./#...#/#...#/####.",
	'C': ".###./#...#/#..../#..../#..../#...#/.###.",
	'D': "###../#..#./#...#/#...#/#...#/#..#./###..",
	'E': "#####/#..../#..../####./#..../#..../#####",
	'F': "#####/#..../#..../####./#..../#..../#....",
	'G': ".###./#...#/#..../#.###/#...#/#...#/.###.",
	'H': "#...#/#...#/#...#/#####/#...#/#...#/#...#",
	'J': "..###/...#./...#./...#./...#./#..#./.##..",
	'K': "#...#/#..#./#.#../##.../#.#../#..#./#...#",
	'M': "#...#/##.##/#.#.#/#.#.#/#...#/#...#/#...#",
	'N': "#...#/##..#/#.#.#/#..##/#...#/#...#/#...#",
	'P': "####./#...#/#...#/####./#..../#..../#....",
	'Q': ".###./#...#/#...#/#...#/#.#.#/#..#./.##.#",
	'R': "####./#...#/#...#/####./#.#../#..#./#...#",
	'S': ".###./#...#/#..../.###./....#/#...#/.###.",
	'T': "#####/..#../..#../..#../..#../..#../..#..",
	'U': "#...#/#...#/#...#/#...#/#...#/#...#/.###.",
	'V': "#...#/#...#/#...#/#...#/#...#/.#.#./..#..",
	'W': "#...#/#...#/#...#/#.#.#/#.#.#/##.##/#...#",
	'X': "#...#/#...#/.#.#./..#../.#.#./#...#/#...#",
	'Y': "#...#/#...#/.#.#./..#../..#../..#../..#..",
	'Z': "#####/....#/...#./..#../.#.../#..../#####",
}

// imageBytes 处理 /verify_img.png?t=：token 有效时现派生答案再画图。
func (v *Verifier) imageBytes(ip, token string) []byte {
	parts := strings.Split(token, ".")
	if len(parts) == 3 {
		ts, nonce, sig := parts[0], parts[1], parts[2]
		if hmac.Equal([]byte(sig), []byte(v.signToken(ip, ts, nonce))) {
			age := float64(time.Now().Unix()) - asFloatNum(ts)
			if age >= 0 && age <= verifyTokenTTL {
				return v.codePNG(v.codeFor(ip, ts, nonce))
			}
		}
	}
	return v.codePNG(v.randomCode())
}

func seedOf(s string) int64 {
	var h uint32 = 2166136261
	for i := 0; i < len(s); i++ {
		h ^= uint32(s[i])
		h *= 16777619
	}
	return int64(h)
}

// codePNG 把验证码画成灰度 PNG：点阵 + 波浪/斜切扭曲 + 噪点 + 干扰线。
func (v *Verifier) codePNG(code string) []byte {
	const (
		scale = 7
		gap   = 7
		cw    = 5 * scale
		ch    = 7 * scale
	)
	width := 20 + verifyCodeLen*cw + (verifyCodeLen-1)*gap
	height := ch + 26
	rnd := rand.New(rand.NewSource(seedOf(code)))

	img := make([][]uint8, height)
	for y := range img {
		img[y] = make([]uint8, width)
		for x := range img[y] {
			img[y][x] = uint8(228 + rnd.Intn(27))
		}
	}
	// 底噪
	for i := 0; i < 360; i++ {
		x, y := rnd.Intn(width), rnd.Intn(height)
		tones := []uint8{198, 208, 216, 224, 232}
		img[y][x] = tones[rnd.Intn(len(tones))]
	}
	// 笔画：逐像素反向采样
	for index := 0; index < len(code); index++ {
		rows, ok := verifyFont[code[index]]
		if !ok {
			continue
		}
		bits := strings.Split(rows, "/")
		ox := 10 + index*(cw+gap) + rnd.Intn(5) - 2
		oy := 13 + rnd.Intn(11) - 5
		phase := rnd.Float64() * 6.283
		wave := 0.05 + rnd.Float64()*0.04
		skew := -0.10 + rnd.Float64()*0.20
		rot := -0.08 + rnd.Float64()*0.16
		squash := 0.90 + rnd.Float64()*0.20
		tone := uint8(20 + rnd.Intn(35))
		for yy := 0; yy < ch; yy++ {
			ny := (float64(yy) + 0.5) / ch
			for xx := 0; xx < cw; xx++ {
				nx := (float64(xx) + 0.5) / cw
				ny2 := ny + rot*(nx-0.5)
				row := int(math.Pow(math.Max(0, math.Min(1, ny2)), squash) * 7)
				if row < 0 || row > 6 {
					continue
				}
				line := bits[row]
				nx2 := nx + wave*math.Sin(ny2*5.4+phase) + skew*(ny2-0.5)
				col := int(nx2 * 5)
				if col < 0 || col > 4 || line[col] != '#' {
					continue
				}
				x, y := ox+xx, oy+yy
				if x >= 0 && x < width && y >= 0 && y < height {
					img[y][x] = tone + uint8(rnd.Intn(21))
				}
			}
		}
	}
	// 干扰线
	for i := 0; i < 10; i++ {
		x0, y0 := rnd.Intn(width), rnd.Intn(height)
		x1 := x0 + rnd.Intn(width) - width/2
		if x1 < 0 {
			x1 = 0
		}
		if x1 > width-1 {
			x1 = width - 1
		}
		y1 := rnd.Intn(height)
		tones := []uint8{142, 158, 172, 186}
		tone := tones[rnd.Intn(len(tones))]
		thick := 1
		if i >= 6 {
			thick = 2
		}
		steps := maxInt(absInt(x1-x0), absInt(y1-y0))
		if steps < 1 {
			steps = 1
		}
		for k := 0; k <= steps; k++ {
			x := x0 + (x1-x0)*k/steps
			y := y0 + (y1-y0)*k/steps
			for t := 0; t < thick; t++ {
				if x >= 0 && x < width && y+t >= 0 && y+t < height {
					img[y+t][x] = tone
				}
			}
		}
	}
	// 正弦干扰
	for i := 0; i < 4; i++ {
		yb := rnd.Intn(height)
		amp := 2.5 + rnd.Float64()*4.5
		freq := 0.05 + rnd.Float64()*0.09
		ph := rnd.Float64() * 6.283
		tones := []uint8{154, 170, 184, 198}
		tone := tones[rnd.Intn(len(tones))]
		for x := 0; x < width; x++ {
			y := int(float64(yb) + amp*math.Sin(float64(x)*freq+ph))
			if y >= 0 && y < height {
				img[y][x] = tone
			}
		}
	}

	canvas := image.NewGray(image.Rect(0, 0, width, height))
	for y := 0; y < height; y++ {
		for x := 0; x < width; x++ {
			canvas.SetGray(x, y, color.Gray{Y: img[y][x]})
		}
	}
	var buf bytes.Buffer
	_ = png.Encode(&buf, canvas)
	return buf.Bytes()
}

// ---------- 浏览器版 HTML ----------

const verifyStyle = `:root{color-scheme:light dark}*{box-sizing:border-box}body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#f6f7f9;color:#1f2328;font-family:system-ui,-apple-system,"Segoe UI",Roboto,"Microsoft YaHei UI",sans-serif;padding:24px}.card{width:100%;max-width:420px;background:#fff;border-radius:16px;padding:28px 24px;box-shadow:0 10px 30px rgba(31,35,40,.08);border:1px solid #e6e8eb}h1{font-size:20px;margin:0 0 6px}p{font-size:13px;line-height:1.7;color:#5b6470;margin:0 0 16px}.code{display:flex;justify-content:center;margin:6px 0 14px}input[type=text]{width:100%;padding:12px 14px;font-size:18px;letter-spacing:.24em;text-transform:uppercase;border:1px solid #d5d9df;border-radius:10px;outline:none;font-family:ui-monospace,Consolas,monospace}input[type=text]:focus{border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.15)}button{width:100%;margin-top:12px;padding:12px;font-size:15px;font-weight:600;color:#fff;background:#2f6feb;border:0;border-radius:10px;cursor:pointer}button:active{transform:translateY(1px)}.err{margin:10px 0 0;padding:10px 12px;border-radius:10px;background:#fff1f0;color:#b42318;font-size:13px;border:1px solid #ffd6d1}.ok{margin:10px 0 0;padding:10px 12px;border-radius:10px;background:#ecfdf3;color:#067647;font-size:13px;border:1px solid #c6f2d8}.tip{margin-top:14px;font-size:12px;color:#8b93a1}`

// _svg 把验证码画成 SVG（浏览器页面用）。
func (v *Verifier) codeSVG(code string) string {
	rnd := rand.New(rand.NewSource(seedOf(code)))
	var parts strings.Builder
	for index := 0; index < len(code); index++ {
		x := 28 + index*44
		y := 64 + rnd.Intn(11) - 5
		rot := rnd.Intn(33) - 16
		parts.WriteString(`<text x="` + itoa(x) + `" y="` + itoa(y) + `" transform="rotate(` + itoa(rot) + ` ` + itoa(x) + ` ` + itoa(y) + `)" font-family="ui-monospace, Consolas, monospace" font-size="40" font-weight="700" fill="#2b2f36">` + html.EscapeString(string(code[index])) + `</text>`)
	}
	for i := 0; i < 2; i++ {
		x1, y1 := rnd.Intn(251), 8+rnd.Intn(85)
		x2, y2 := rnd.Intn(251), 8+rnd.Intn(85)
		parts.WriteString(`<line x1="` + itoa(x1) + `" y1="` + itoa(y1) + `" x2="` + itoa(x2) + `" y2="` + itoa(y2) + `" stroke="#8b93a1" stroke-width="1.4" opacity="0.65"/>`)
	}
	return `<svg width="250" height="100" viewBox="0 0 250 100" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="验证码"><rect width="250" height="100" rx="10" fill="#f2f4f7"/>` + parts.String() + `</svg>`
}

// pageHTML 浏览器版验证页（看图输入）。
func (v *Verifier) pageHTML(ip, base, lang, errMsg, token string) string {
	if token == "" {
		token, _ = v.makeToken(ip)
	}
	t := func(key string) string { return v.srv.i18n.Tlang(key, lang) }
	errHTML := ""
	if errMsg != "" {
		errHTML = `<div class="err">` + html.EscapeString(errMsg) + `</div>`
	}
	return `<!doctype html><html lang="` + html.EscapeString(lang) + `"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>` + html.EscapeString(t("verify.page_title")) + `</title><style>` + verifyStyle + `</style></head><body><form class="card" method="get" action="/verify" autocomplete="off"><h1>` + html.EscapeString(t("verify.page_title")) + `</h1><p>` + html.EscapeString(t("verify.page_hint")) + `</p><div class="code">` + v.codeSVG(v.codeForToken(ip, token)) + `</div><input type="hidden" name="token" value="` + html.EscapeString(token) + `"><input type="text" name="answer" maxlength="7" autofocus autocomplete="off" inputmode="text" placeholder="` + html.EscapeString(t("verify.placeholder")) + `">` + errHTML + `<button type="submit">` + html.EscapeString(t("verify.button")) + `</button><div class="tip">` + html.EscapeString(t("verify.page_tip")) + `</div></form></body></html>`
}

// codeForToken 从 token 派生答案（token 无效时返回空串）。
func (v *Verifier) codeForToken(ip, token string) string {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return v.randomCode()
	}
	if !hmac.Equal([]byte(parts[2]), []byte(v.signToken(ip, parts[0], parts[1]))) {
		return v.randomCode()
	}
	return v.codeFor(ip, parts[0], parts[1])
}

func (v *Verifier) okHTML(ip, base, lang string) string {
	t := func(key string) string { return v.srv.i18n.Tlang(key, lang) }
	return `<!doctype html><html lang="` + html.EscapeString(lang) + `"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>` + html.EscapeString(t("verify.page_title")) + `</title><style>` + verifyStyle + `</style></head><body><div class="card"><h1>` + html.EscapeString(t("verify.ok_title")) + `</h1><p>` + html.EscapeString(t("verify.ok_desc")) + `</p><div class="ok">` + html.EscapeString(t("verify.ok_line")) + `</div></div></body></html>`
}

func (v *Verifier) cfHTML(ip, base, lang, token, errMsg string) string {
	t := func(key string) string { return v.srv.i18n.Tlang(key, lang) }
	tokenURL := url.QueryEscape(token)
	errHTML := ""
	if errMsg != "" {
		errHTML = `<div class="err">` + html.EscapeString(errMsg) + `</div>`
	}
	script := `<script>function cfDone(t){location.href='/verify_cf_post?c=` + html.EscapeString(html.EscapeString(tokenURL)) + `&r='+encodeURIComponent(t);}</script>`
	return `<!doctype html><html lang="` + html.EscapeString(lang) + `"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>` + html.EscapeString(t("verify.cf_page_title")) + `</title><style>` + verifyStyle + `</style><script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script></head><body><div class="card"><h1>` + html.EscapeString(t("verify.cf_page_title")) + `</h1><p>` + html.EscapeString(t("verify.cf_page_desc")) + `</p><div style="display:flex;justify-content:center;margin:18px 0"><div class="cf-turnstile" data-sitekey="` + html.EscapeString(v.srv.cfg.TurnstileSiteKey) + `" data-callback="cfDone"></div></div>` + errHTML + `<div class="tip">` + html.EscapeString(t("verify.cf_ok")) + `</div></div>` + script + `</body></html>`
}

// ---------- PCL 侧 XAML ----------

// cfCard 连续错够次数后的极简卡：一句话 + 去浏览器验证。
func (v *Verifier) cfCard(ip, base, lang string) string {
	t := func(key string) string { return v.srv.i18n.Tlang(key, lang) }
	link := base + "/verify_cf?c=" + url.QueryEscape(v.cfToken(ip))
	return `<local:MyCard Title="` + escapeAttr(t("verify.cf_title")) + `" CanSwap="False"><StackPanel Margin="25,40,23,20"><TextBlock Text="` + escapeAttr(t("verify.cf_hint")) + `" FontSize="12" TextWrapping="Wrap" HorizontalAlignment="Center" Foreground="{DynamicResource ColorBrush3}" /><local:MyTextButton Height="38" Margin="0,18,0,0" HorizontalAlignment="Center" Text="` + escapeAttr(t("verify.cf_link")) + `" EventType="打开网页" EventData="` + escapeURL(link) + `" /></StackPanel></local:MyCard>`
}

// guideXAML PCL 侧验证页（pclce=true 时用浏览器验证按钮，其余为输入框 + 提交）。
func (v *Verifier) guideXAML(ip, lang, base string, pclce bool) string {
	t := func(key string) string { return v.srv.i18n.Tlang(key, lang) }
	if v.needTurnstile(ip) {
		return v.cfCard(ip, base, lang)
	}
	token, _ := v.makeToken(ip)
	image := base + "/verify_img.png?t=" + url.QueryEscape(token)
	var body string
	if pclce {
		body = `<local:MyImage Source="` + escapeURL(image) + `" Height="76" HorizontalAlignment="Center" Margin="0,14,0,0" />` +
			`<TextBlock Text="` + escapeAttr("请点击下方按钮，在浏览器中完成人机验证；完成后回到 PCL，点「刷新页面」即可进入主页。") + `" FontSize="14" TextWrapping="Wrap" HorizontalAlignment="Center" Foreground="{DynamicResource ColorBrush3}" Margin="0,14,0,0" />` +
			`<local:MyIconTextButton Height="40" Margin="0,12,0,0" HorizontalAlignment="Center" Text="` + escapeAttr("在浏览器中验证") + `" ColorType="Highlight" EventType="打开网页" EventData="` + escapeURL(base+"/verify") + `" />`
	} else {
		fmtStr := "{Binding Path=Text,ElementName=vcode,StringFormat='{}" + base + "/verify_submit.json?t=" + token + "&q={0}'}"
		body = `<local:MyImage Source="` + escapeURL(image) + `" Height="76" HorizontalAlignment="Center" Margin="0,14,0,0" />` +
			`<Border Margin="0,12,0,0" Height="38" Width="260" HorizontalAlignment="Center" Background="{DynamicResource ColorBrush7}" CornerRadius="5"><local:MyTextBox x:Name="vcode" Height="38" Margin="10,0" HintText="` + escapeAttr(t("verify.img_hint")) + `" Foreground="{DynamicResource ColorBrush2}" VerticalAlignment="Center" /></Border>` +
			`<local:MyIconTextButton Height="40" Margin="0,12,0,0" HorizontalAlignment="Center" Text="` + escapeAttr(t("verify.submit")) + `" LogoScale="0.85" ColorType="Highlight" Logo="` + escapeAttr(refreshIcon) + `" EventType="打开帮助" EventData="` + escapeURL(fmtStr) + `" />` +
			`<TextBlock Text="` + escapeAttr(t("verify.refresh_tip")) + `" FontSize="16" TextWrapping="Wrap" HorizontalAlignment="Center" Foreground="#FF000000" Margin="0,16,0,0" />`
	}
	return `<local:MyCard Title="` + escapeAttr(t("verify.title")) + `" CanSwap="False"><StackPanel Margin="25,40,23,20"><TextBlock Text="` + escapeAttr(t("verify.need_title")) + `" FontSize="24" FontWeight="Bold" HorizontalAlignment="Center" Foreground="#FF000000" />` + body +
		`<local:MyIconTextButton Height="36" Margin="0,14,0,0" HorizontalAlignment="Center" Text="` + escapeAttr(t("verify.refresh")) + `" LogoScale="0.85" Logo="` + escapeAttr(refreshIcon) + `" EventType="刷新页面" EventData="-" /></StackPanel></local:MyCard>`
}

// submitXAML PCL 点提交后打开的页面：判定、记账、给结果。
func (v *Verifier) submitXAML(ip, lang, base, token, answer string) string {
	t := func(key string) string { return v.srv.i18n.Tlang(key, lang) }
	ok, reason := v.checkToken(ip, token, answer)
	var head, desc, extra, bottom string
	if ok {
		v.markVerified(ip)
		v.clearFail(ip)
		logDebug("[Verify] %s 通过访问验证（PCL 内）", ip)
		head = t("verify.ok_title")
		days := v.srv.cfg.VerifyDays
		if days <= 0 {
			days = 7
		}
		desc = strings.ReplaceAll(t("verify.ok_page_desc"), "{days}", itoa(days))
		extra = `<local:MyIconTextButton Height="40" Margin="0,18,0,0" HorizontalAlignment="Center" Text="` + escapeAttr(t("verify.ok_back")) + `" LogoScale="0.85" ColorType="Highlight" Logo="` + escapeAttr(refreshIcon) + `" EventType="切换页面" EventData="Launch" />` +
			`<TextBlock Text="` + escapeAttr(t("verify.ok_refresh_hint")) + `" FontSize="11" TextWrapping="Wrap" HorizontalAlignment="Center" Foreground="{DynamicResource ColorBrush3}" Margin="0,12,0,0" />`
	} else {
		count := v.noteFail(ip)
		head = t("verify.fail_title")
		if count >= verifyFailForCF && v.cfConfigured() {
			return v.cfCard(ip, base, lang)
		}
		bottom = `<local:MyIconTextButton Height="36" Margin="0,16,0,0" HorizontalAlignment="Center" Text="` + escapeAttr(t("verify.refresh")) + `" LogoScale="0.85" Logo="` + escapeAttr(refreshIcon) + `" EventType="刷新页面" EventData="-" />`
		left := verifyFailForCF - count
		if left < 0 {
			left = 0
		}
		if reason == "expired" {
			desc = t("verify.expired")
		} else {
			desc = t("verify.wrong_left")
			desc = strings.ReplaceAll(desc, "{n}", itoa(left))
		}
		token, _ = v.makeToken(ip)
		image := base + "/verify_img.png?t=" + url.QueryEscape(token)
		fmtStr := "{Binding Path=Text,ElementName=vsb,StringFormat='{}" + base + "/verify_submit.json?t=" + token + "&q={0}'}"
		extra = `<local:MyImage Source="` + escapeURL(image) + `" Height="76" HorizontalAlignment="Center" Margin="0,12,0,0" />` +
			`<Border Margin="0,12,0,0" Height="38" Width="260" HorizontalAlignment="Center" Background="{DynamicResource ColorBrush7}" CornerRadius="5"><local:MyTextBox x:Name="vsb" Height="38" Margin="10,0" HintText="` + escapeAttr(t("verify.img_hint")) + `" Foreground="{DynamicResource ColorBrush2}" VerticalAlignment="Center" /></Border>` +
			`<local:MyIconTextButton Height="40" Margin="0,12,0,0" HorizontalAlignment="Center" Text="` + escapeAttr(t("verify.submit")) + `" LogoScale="0.85" ColorType="Highlight" Logo="` + escapeAttr(refreshIcon) + `" EventType="打开帮助" EventData="` + escapeURL(fmtStr) + `" />`
	}
	return `<local:MyCard Title="` + escapeAttr(t("verify.title")) + `" CanSwap="False"><StackPanel Margin="25,40,23,20"><TextBlock Text="` + escapeAttr(head) + `" FontSize="22" FontWeight="Bold" HorizontalAlignment="Center" Foreground="#FF000000" /><TextBlock Text="` + escapeAttr(desc) + `" FontSize="11" TextWrapping="Wrap" Foreground="{DynamicResource ColorBrush3}" Margin="0,10,0,0" />` + extra + bottom + `</StackPanel></local:MyCard>`
}

// refreshIcon 是 PCL 内置「刷新」几何图标（用于「提交」「刷新页面」按钮）。
const refreshIcon = "M480 96 H544 V480 L896 480 V544 H480 Z"

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var buf [20]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		buf[i] = '-'
	}
	return string(buf[i:])
}

func absInt(n int) int {
	if n < 0 {
		return -n
	}
	return n
}
