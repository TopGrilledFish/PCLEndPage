package main

import (
	"crypto/md5"
	"encoding/hex"
	"fmt"
	"math"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf16"
)

// 计算器：输入解析、各计算器逻辑与结果行。与旧版 calc.py 逐条对齐。

type calcRow struct {
	Label string
	Value string
}

type calcItem struct {
	ID   string
	Icon string
	Run  func(s *Server, raw, lang string) ([]calcRow, error)
}

// floatsOf 抠出输入里的数字；空格、逗号、分号、顿号都是分隔符。
func floatsOf(raw string) ([]float64, bool) {
	text := raw
	for _, ch := range []string{"，", ",", ";", "；", "、", "\t"} {
		text = strings.ReplaceAll(text, ch, " ")
	}
	fields := strings.Fields(text)
	out := make([]float64, 0, len(fields))
	for _, f := range fields {
		v, err := strconv.ParseFloat(f, 64)
		if err != nil {
			return nil, false
		}
		out = append(out, v)
	}
	return out, true
}

// fnum 整数不显示小数点，其他最多 4 位再去尾零。
func fnum(v float64) string {
	if math.Abs(v-math.RoundToEven(v)) < 1e-9 {
		return strconv.FormatInt(int64(math.RoundToEven(v)), 10)
	}
	s := strconv.FormatFloat(v, 'f', 4, 64)
	return strings.TrimRight(strings.TrimRight(s, "0"), ".")
}

func calcFloats(s *Server, raw string, count int, what, lang string) ([]float64, error) {
	nums, ok := floatsOf(raw)
	if !ok {
		return nil, fmt.Errorf("%s", s.i18n.Tf("calc.err.numbers", lang))
	}
	if len(nums) < count {
		return nil, fmt.Errorf("%s", s.i18n.Tf("calc.err.need", lang, "count", strconv.Itoa(count), "what", what))
	}
	return nums, nil
}

// damageFields 按逗号位置拆伤害参数，空着返回 nil（不能丢，丢了会串位）。
func damageFields(raw string, count int) ([]*float64, error, string) {
	text := raw
	for _, ch := range []string{"，", ";", "；", "\t"} {
		text = strings.ReplaceAll(text, ch, ",")
	}
	parts := strings.Split(text, ",")
	nums := make([]*float64, 0, count)
	for i, part := range parts {
		if i >= count {
			break
		}
		part = strings.TrimSpace(part)
		if part == "" {
			nums = append(nums, nil)
			continue
		}
		v, err := strconv.ParseFloat(part, 64)
		if err != nil {
			return nil, nil, "calc.err.each_number"
		}
		nums = append(nums, &v)
	}
	for len(nums) < count {
		nums = append(nums, nil)
	}
	return nums, nil, ""
}

func calcNether(s *Server, raw, lang string) ([]calcRow, error) {
	nums, err := calcFloats(s, raw, 2, s.i18n.Tf("calc.nether.what", lang), lang)
	if err != nil {
		return nil, err
	}
	var x, z, y float64
	hasY := len(nums) >= 3
	if hasY {
		x, y, z = nums[0], nums[1], nums[2]
	} else {
		x, z = nums[0], nums[1]
	}
	rows := []calcRow{
		{s.i18n.Tf("calc.nether.to_nether", lang), "X " + fnum(x/8) + "   Z " + fnum(z/8)},
		{s.i18n.Tf("calc.nether.to_over", lang), "X " + fnum(x*8) + "   Z " + fnum(z*8)},
	}
	if hasY {
		rows = append(rows, calcRow{"Y", s.i18n.Tf("calc.nether.y_note", lang, "n", fnum(y))})
	}
	rows = append(rows, calcRow{s.i18n.Tf("calc.nether.ratio", lang), s.i18n.Tf("calc.nether.ratio_value", lang)})
	return rows, nil
}

func calcChunk(s *Server, raw, lang string) ([]calcRow, error) {
	nums, err := calcFloats(s, raw, 2, "X,Z", lang)
	if err != nil {
		return nil, err
	}
	x, z := nums[0], nums[1]
	cx, cz := math.Floor(x/16), math.Floor(z/16)
	return []calcRow{
		{s.i18n.Tf("calc.chunk.pos", lang), "X " + fnum(x) + "   Z " + fnum(z)},
		{s.i18n.Tf("calc.chunk.chunk", lang), "X " + strconv.Itoa(int(cx)) + "   Z " + strconv.Itoa(int(cz))},
		{s.i18n.Tf("calc.chunk.offset", lang), "X " + fnum(x-cx*16) + "   Z " + fnum(z-cz*16) + s.i18n.Tf("calc.chunk.offset_note", lang)},
		{s.i18n.Tf("calc.chunk.region", lang), "r." + strconv.Itoa(int(math.Floor(cx/32))) + "." + strconv.Itoa(int(math.Floor(cz/32))) + ".mca"},
		{s.i18n.Tf("calc.chunk.range", lang), "X " + strconv.Itoa(int(cx*16)) + "–" + strconv.Itoa(int(cx*16+15)) + "   Z " + strconv.Itoa(int(cz*16)) + "–" + strconv.Itoa(int(cz*16+15))},
	}, nil
}

func calcDistance(s *Server, raw, lang string) ([]calcRow, error) {
	nums, ok := floatsOf(raw)
	if !ok {
		return nil, fmt.Errorf("%s", s.i18n.Tf("calc.distance.what", lang))
	}
	var x1, y1, z1, x2, y2, z2 float64
	flatOnly := false
	switch {
	case len(nums) >= 6:
		x1, y1, z1, x2, y2, z2 = nums[0], nums[1], nums[2], nums[3], nums[4], nums[5]
	case len(nums) == 4:
		x1, z1, x2, z2 = nums[0], nums[1], nums[2], nums[3]
		y1, y2 = 0, 0
		flatOnly = true
	case len(nums) == 5:
		return nil, fmt.Errorf("%s", s.i18n.Tf("calc.distance.count", lang, "n", strconv.Itoa(len(nums))))
	default:
		return nil, fmt.Errorf("%s", s.i18n.Tf("calc.distance.what", lang))
	}
	dx, dy, dz := x2-x1, y2-y1, z2-z1
	flat := math.Hypot(dx, dz)
	rows := []calcRow{
		{s.i18n.Tf("calc.distance.flat", lang), s.i18n.Tf("calc.blocks", lang, "n", fnum(flat))},
		{s.i18n.Tf("calc.distance.straight", lang), s.i18n.Tf("calc.blocks", lang, "n", fnum(math.Sqrt(dx*dx+dy*dy+dz*dz)))},
		{"Δ", "X " + fnum(dx) + "   Y " + fnum(dy) + "   Z " + fnum(dz)},
		{s.i18n.Tf("calc.distance.via_nether", lang), s.i18n.Tf("calc.distance.via_nether_value", lang, "flat", fnum(flat), "nether", fnum(flat/8))},
	}
	if flatOnly {
		rows = append(rows, calcRow{s.i18n.Tf("calc.note", lang), s.i18n.Tf("calc.distance.flat_only", lang)})
	}
	return rows, nil
}

func calcTick(s *Server, raw, lang string) ([]calcRow, error) {
	nums, err := calcFloats(s, raw, 1, s.i18n.Tf("calc.tick.what", lang), lang)
	if err != nil {
		return nil, err
	}
	ticks := nums[0]
	return []calcRow{
		{s.i18n.Tf("calc.tick.ticks", lang), fnum(ticks)},
		{s.i18n.Tf("calc.tick.seconds", lang), fnum(ticks / 20)},
		{s.i18n.Tf("calc.tick.minutes", lang), fnum(ticks / 1200)},
		{s.i18n.Tf("calc.tick.redstone", lang), fnum(ticks/2) + s.i18n.Tf("calc.tick.redstone_note", lang)},
		{s.i18n.Tf("calc.tick.days", lang), fnum(ticks/24000) + s.i18n.Tf("calc.tick.days_note", lang)},
	}, nil
}

func calcExp(s *Server, raw, lang string) ([]calcRow, error) {
	nums, err := calcFloats(s, raw, 1, s.i18n.Tf("calc.exp.what", lang), lang)
	if err != nil {
		return nil, err
	}
	var start, level int
	if len(nums) >= 2 {
		start, level = int(nums[0]), int(nums[1])
	} else {
		start, level = 0, int(nums[0])
	}
	if start < 0 || level < 0 {
		return nil, fmt.Errorf("%s", s.i18n.Tf("calc.exp.negative", lang))
	}
	if level < start {
		start, level = level, start
	}
	total := func(n int) int {
		if n <= 16 {
			return n*n + 6*n
		}
		if n <= 31 {
			return int(math.RoundToEven(2.5*float64(n*n) - 40.5*float64(n) + 360))
		}
		return int(math.RoundToEven(4.5*float64(n*n) - 162.5*float64(n) + 2220))
	}
	points := func(n int) string { return s.i18n.Tf("calc.exp.points", lang, "n", strconv.Itoa(n)) }
	rows := []calcRow{
		{s.i18n.Tf("calc.exp.from_zero", lang, "level", strconv.Itoa(level)), points(total(level))},
	}
	if start > 0 {
		rows = append(rows, calcRow{
			s.i18n.Tf("calc.exp.between", lang, "a", strconv.Itoa(start), "b", strconv.Itoa(level)),
			points(total(level) - total(start)),
		})
	}
	rows = append(rows,
		calcRow{s.i18n.Tf("calc.exp.next", lang), points(total(level+1) - total(level))},
		calcRow{s.i18n.Tf("calc.exp.convert", lang), s.i18n.Tf("calc.exp.convert_value", lang, "n", strconv.Itoa(total(1)))},
	)
	return rows, nil
}

func calcArmor(s *Server, raw, lang string) ([]calcRow, error) {
	nums, err := calcFloats(s, raw, 2, s.i18n.Tf("calc.armor.what", lang), lang)
	if err != nil {
		return nil, err
	}
	damage, armor := nums[0], nums[1]
	var toughness, prot float64
	if len(nums) >= 3 {
		toughness = nums[2]
	}
	if len(nums) >= 4 {
		prot = nums[3]
	}
	if damage <= 0 {
		return nil, fmt.Errorf("%s", s.i18n.Tf("calc.armor.positive", lang))
	}
	effective := math.Min(20, math.Max(armor/5, armor-damage/(2+toughness/4)))
	after := damage * (1 - effective/25)
	epf := math.Min(20, prot)
	final := after * (1 - epf/25)
	protNote := ""
	if prot != 0 {
		protNote = s.i18n.Tf("calc.armor.prot_note", lang, "level", fnum(prot))
	}
	return []calcRow{
		{s.i18n.Tf("calc.armor.raw", lang), fnum(damage)},
		{s.i18n.Tf("calc.armor.effective", lang), s.i18n.Tf("calc.armor.effective_note", lang, "eff", fnum(effective), "armor", fnum(armor), "toughness", fnum(toughness))},
		{s.i18n.Tf("calc.armor.after_armor", lang), fnum(after)},
		{s.i18n.Tf("calc.armor.after_ench", lang), fnum(final) + protNote},
		{s.i18n.Tf("calc.armor.total", lang), fnum((1-final/damage)*100) + "%"},
	}, nil
}

func calcStack(s *Server, raw, lang string) ([]calcRow, error) {
	nums, err := calcFloats(s, raw, 1, s.i18n.Tf("calc.stack.what", lang), lang)
	if err != nil {
		return nil, err
	}
	n := int(nums[0])
	if n < 0 {
		return nil, fmt.Errorf("%s", s.i18n.Tf("calc.stack.negative", lang))
	}
	stacks := n / 64
	rest := n % 64
	fill := func(capacity int, fullKey, restKey string) string {
		full := n / capacity
		left := n % capacity
		need := full
		if left > 0 {
			need++
		}
		if left == 0 {
			return s.i18n.Tf(fullKey, lang, "full", strconv.Itoa(full), "need", strconv.Itoa(need))
		}
		leftStacks := left / 64
		leftItems := left % 64
		return s.i18n.Tf(restKey, lang, "full", strconv.Itoa(full), "need", strconv.Itoa(need),
			"stacks", strconv.Itoa(leftStacks), "items", strconv.Itoa(leftItems))
	}
	stacksText := s.i18n.Tf("calc.stack.stack_unit", lang, "n", strconv.Itoa(stacks))
	if rest > 0 {
		stacksText += s.i18n.Tf("calc.stack.and_n", lang, "n", strconv.Itoa(rest))
	}
	return []calcRow{
		{s.i18n.Tf("calc.stack.count", lang), strconv.Itoa(n)},
		{s.i18n.Tf("calc.stack.stacks", lang), stacksText},
		{s.i18n.Tf("calc.stack.boxes", lang), fill(64*27, "calc.stack.box_full", "calc.stack.box_rest")},
		{s.i18n.Tf("calc.stack.chests", lang), fill(64*54, "calc.stack.chest_full", "calc.stack.chest_rest")},
	}, nil
}

var hexColorRE = regexp.MustCompile(`^(?:#|0x)?([0-9a-fA-F]{6})$`)

func calcColor(s *Server, raw, lang string) ([]calcRow, error) {
	text := strings.TrimSpace(raw)
	if text == "" {
		return nil, fmt.Errorf("%s", s.i18n.Tf("calc.color.empty", lang))
	}
	var value int64
	if m := hexColorRE.FindStringSubmatch(text); m != nil {
		v, err := strconv.ParseInt(m[1], 16, 64)
		if err != nil {
			return nil, fmt.Errorf("%s", s.i18n.Tf("calc.color.bad", lang))
		}
		value = v
	} else {
		v, err := strconv.ParseInt(text, 10, 64)
		if err != nil {
			return nil, fmt.Errorf("%s", s.i18n.Tf("calc.color.bad", lang))
		}
		value = v
	}
	if value < 0 || value > 0xFFFFFF {
		return nil, fmt.Errorf("%s", s.i18n.Tf("calc.color.range", lang))
	}
	hexed := fmt.Sprintf("#%06X", value)
	return []calcRow{
		{s.i18n.Tf("calc.color.hex", lang), hexed},
		{s.i18n.Tf("calc.color.dec", lang), strconv.FormatInt(value, 10)},
		{s.i18n.Tf("calc.color.command", lang), s.i18n.Tf("calc.color.command_value", lang, "hex", hexed, "dec", strconv.FormatInt(value, 10))},
		{"RGB", fmt.Sprintf("R %d   G %d   B %d", (value>>16)&0xFF, (value>>8)&0xFF, value&0xFF)},
	}, nil
}

func calcUUID(s *Server, raw, lang string) ([]calcRow, error) {
	name := strings.TrimSpace(raw)
	if name == "" {
		return nil, fmt.Errorf("%s", s.i18n.Tf("calc.uuid.empty", lang))
	}
	sum := md5.Sum([]byte("OfflinePlayer:" + name))
	sum[6] = (sum[6] & 0x0F) | 0x30
	sum[8] = (sum[8] & 0x3F) | 0x80
	hexed := hex.EncodeToString(sum[:])
	dashed := hexed[0:8] + "-" + hexed[8:12] + "-" + hexed[12:16] + "-" + hexed[16:20] + "-" + hexed[20:32]
	return []calcRow{
		{s.i18n.Tf("calc.uuid.player", lang), name},
		{s.i18n.Tf("calc.uuid.uuid", lang), dashed},
		{s.i18n.Tf("calc.uuid.dashed", lang), hexed},
		{s.i18n.Tf("calc.note", lang), s.i18n.Tf("calc.uuid.note_value", lang)},
	}, nil
}

// javaHash Java String.hashCode，按 UTF-16 码元算。
func javaHash(text string) int32 {
	units := utf16.Encode([]rune(text))
	var h uint32
	for _, u := range units {
		h = 31*h + uint32(u)
	}
	return int32(h)
}

func calcSeed(s *Server, raw, lang string) ([]calcRow, error) {
	text := raw
	if strings.TrimSpace(text) == "" {
		return nil, fmt.Errorf("%s", s.i18n.Tf("calc.seed.empty", lang))
	}
	h := javaHash(text)
	return []calcRow{
		{s.i18n.Tf("calc.seed.text", lang), strings.TrimSpace(text)},
		{s.i18n.Tf("calc.seed.value", lang), strconv.FormatInt(int64(h), 10)},
		{s.i18n.Tf("calc.seed.unsigned", lang), strconv.FormatInt(int64(uint32(h)), 10)},
		{s.i18n.Tf("calc.note", lang), s.i18n.Tf("calc.seed.note_value", lang)},
	}, nil
}

// ---- 要塞 ----

const strongholdCount = 128

const javaMult = 0x5DEECE66D
const javaAdd = 0xB
const javaMask = (1 << 48) - 1

type javaRandom struct {
	state int64
}

func newJavaRandom(seed int64) *javaRandom {
	return &javaRandom{state: (seed ^ javaMult) & javaMask}
}

func (r *javaRandom) next(bits int) int64 {
	r.state = (r.state*javaMult + javaAdd) & javaMask
	return r.state >> (48 - bits)
}

func (r *javaRandom) nextDouble() float64 {
	return float64((r.next(26)<<27)+r.next(27)) / float64(int64(1)<<53)
}

func (r *javaRandom) nextLong() int64 {
	high := uint64(r.next(32))
	low := uint64(r.next(32))
	return int64((high << 32) + low)
}

func roundHalfAway(v float64) int64 {
	if v >= 0 {
		return int64(math.Floor(v + 0.5))
	}
	return int64(math.Ceil(v - 0.5))
}

func strongholdPos(angle, dist float64) (int64, int64) {
	x := roundHalfAway(math.Cos(angle)*dist)*16 + 8
	z := roundHalfAway(math.Sin(angle)*dist)*16 + 8
	return (x & ^15) + 4, (z & ^15) + 4
}

func strongholds(seed int64) [][2]int64 {
	rnd := newJavaRandom(seed)
	angle := 2 * math.Pi * rnd.nextDouble()
	dist := 128.0 + (rnd.nextDouble()-0.5)*80.0
	out := [][2]int64{}
	x, z := strongholdPos(angle, dist)
	out = append(out, [2]int64{x, z})

	ringnum, ringmax, ringidx, index := 0, 3, 0, 0
	for len(out) < strongholdCount {
		rnd.nextLong()
		ringidx++
		angle += 2 * math.Pi / float64(ringmax)
		if ringidx == ringmax {
			ringnum++
			ringidx = 0
			ringmax = ringmax + 2*ringmax/(ringnum+1)
			if ringmax > strongholdCount-index {
				ringmax = strongholdCount - index
			}
			angle += rnd.nextDouble() * 2 * math.Pi
		}
		dist = 128.0 + 6.0*float64(ringnum)*32.0 + (rnd.nextDouble()-0.5)*80.0
		x, z = strongholdPos(angle, dist)
		out = append(out, [2]int64{x, z})
		index++
	}
	return out
}

var compass8 = []string{"e", "se", "s", "sw", "w", "nw", "n", "ne"}

func compassDir(dx, dz float64, s *Server, lang string) string {
	angle := math.Atan2(dz, dx)
	if angle < 0 {
		angle += 2 * math.Pi
	}
	idx := int((angle+math.Pi/8)/(math.Pi/4)) % 8
	return s.i18n.Tf("calc.dir."+compass8[idx], lang)
}

func seedValue(text string) int32 {
	if v, err := strconv.ParseInt(strings.TrimSpace(text), 10, 64); err == nil {
		return int32(v)
	}
	return javaHash(text)
}

func calcStronghold(s *Server, raw, lang string) ([]calcRow, error) {
	parts := strings.Split(strings.ReplaceAll(raw, "，", ","), ",")
	for i := range parts {
		parts[i] = strings.TrimSpace(parts[i])
	}
	if len(parts) < 3 {
		return nil, fmt.Errorf("%s", s.i18n.Tf("calc.stronghold.what", lang))
	}
	x, err1 := strconv.ParseFloat(parts[0], 64)
	z, err2 := strconv.ParseFloat(parts[1], 64)
	if err1 != nil || err2 != nil {
		return nil, fmt.Errorf("%s", s.i18n.Tf("calc.stronghold.xz", lang))
	}
	if parts[2] == "" {
		return nil, fmt.Errorf("%s", s.i18n.Tf("calc.stronghold.no_seed", lang))
	}
	seed := seedValue(parts[2])

	positions := strongholds(int64(seed))
	bestDist := math.Inf(1)
	bestIndex := 0
	var bestX, bestZ int64
	for i, p := range positions {
		d := math.Hypot(float64(p[0])-x, float64(p[1])-z)
		if d < bestDist {
			bestDist = d
			bestIndex = i + 1
			bestX, bestZ = p[0], p[1]
		}
	}
	return []calcRow{
		{s.i18n.Tf("calc.stronghold.you", lang), fnum(x) + ", " + fnum(z)},
		{s.i18n.Tf("calc.stronghold.seed", lang), strconv.FormatInt(int64(seed), 10)},
		{s.i18n.Tf("calc.stronghold.nearest", lang), strconv.FormatInt(bestX, 10) + ", " + strconv.FormatInt(bestZ, 10)},
		{s.i18n.Tf("calc.stronghold.distance", lang), s.i18n.Tf("calc.blocks", lang, "n", fnum(bestDist))},
		{s.i18n.Tf("calc.stronghold.direction", lang),
			s.i18n.Tf("calc.stronghold.direction_value", lang,
				"dir", compassDir(float64(bestX)-x, float64(bestZ)-z, s, lang),
				"index", strconv.Itoa(bestIndex))},
		{s.i18n.Tf("calc.note", lang), s.i18n.Tf("calc.stronghold.note_value", lang)},
	}, nil
}

// ---- 伤害 ----

var damageParams = []string{"attack", "sharpness", "smite", "bane", "strength", "weakness", "crit", "charge"}

func calcDamage(s *Server, raw, lang string) ([]calcRow, error) {
	if strings.TrimSpace(strings.Trim(raw, " ,，;；\t")) == "" {
		return nil, fmt.Errorf("%s", s.i18n.Tf("calc.damage.min", lang))
	}
	fields, _, problemKey := damageFields(raw, len(damageParams))
	if problemKey != "" {
		return nil, fmt.Errorf("%s", s.i18n.Tf(problemKey, lang))
	}
	filled := func(i int, def float64) float64 {
		if fields[i] == nil {
			return def
		}
		return *fields[i]
	}
	attack := filled(0, 0)
	if attack <= 0 {
		return nil, fmt.Errorf("%s", s.i18n.Tf("calc.damage.min", lang))
	}
	sharp := int64(filled(1, 0))
	smite := int64(filled(2, 0))
	bane := int64(filled(3, 0))
	strength := int64(filled(4, 0))
	weakness := int64(filled(5, 0))
	wantCrit := filled(6, 0) > 0
	charge := math.Min(math.Max(filled(7, 100)/100, 0), 1)

	extra := []string{}
	if sharp > 0 && (smite > 0 || bane > 0) {
		smite, bane = 0, 0
		extra = append(extra, s.i18n.Tf("calc.damage.excl_sharp", lang))
	} else if smite > 0 && bane > 0 {
		bane = 0
		extra = append(extra, s.i18n.Tf("calc.damage.excl_smite", lang))
	}

	base := attack
	for i := int64(0); i < maxInt64(strength, 0); i++ {
		base = base*1.3 + 1
	}
	for i := int64(0); i < maxInt64(weakness, 0); i++ {
		base = base*0.8 - 0.5
	}
	base = math.Min(math.Max(base, 0), 2048)

	bonus := float64(0)
	if sharp > 0 {
		bonus = 0.5*float64(sharp) + 0.5
	}
	bonus += 2.5 * float64(smite)
	bonus += 2.5 * float64(bane)
	bonus = math.Floor(bonus)
	if smite > 0 {
		extra = append(extra, s.i18n.Tf("calc.damage.smite_only", lang))
	}
	if bane > 0 {
		extra = append(extra, s.i18n.Tf("calc.damage.bane_only", lang))
	}
	crit := wantCrit && charge >= 0.9
	if wantCrit && !crit {
		extra = append(extra, s.i18n.Tf("calc.damage.crit_low", lang))
	}
	mult := 1.0
	if crit {
		mult = 1.5
	}
	final := base*(0.2+0.8*charge*charge)*mult + bonus*charge
	points := func(n float64) string { return s.i18n.Tf("calc.damage.points", lang, "n", fnum(n)) }
	rows := []calcRow{{s.i18n.Tf("calc.dmg.attack", lang), points(attack)}}
	if strength != 0 || weakness != 0 {
		rows = append(rows, calcRow{s.i18n.Tf("calc.damage.after_effects", lang), points(base)})
	}
	sign := ""
	if bonus != 0 {
		sign = "+"
	}
	rows = append(rows, calcRow{s.i18n.Tf("calc.damage.enchant_bonus", lang), sign + points(bonus)})
	if charge < 1 {
		rows = append(rows, calcRow{s.i18n.Tf("calc.dmg.charge", lang), fnum(charge*100) + "%"})
	}
	critKey := "calc.damage.crit_no"
	if crit {
		critKey = "calc.damage.crit_yes"
	}
	rows = append(rows, calcRow{s.i18n.Tf("calc.dmg.crit", lang), s.i18n.Tf(critKey, lang)})
	rows = append(rows, calcRow{s.i18n.Tf("calc.damage.final", lang),
		s.i18n.Tf("calc.damage.final_value", lang, "n", fnum(final), "hearts", fnum(final/2))})
	extra = append(extra, s.i18n.Tf("calc.damage.note_value", lang))
	rows = append(rows, calcRow{s.i18n.Tf("calc.note", lang), strings.Join(extra, "")})
	return rows, nil
}

func maxInt64(a, b int64) int64 {
	if a > b {
		return a
	}
	return b
}

// ---- 计算器表 ----

var calcItems = []calcItem{
	{"damage", calcWikiIcon("Strength_JE3_BE2.png"), calcDamage},
	{"nether", calcWikiIcon("Netherrack_JE6_BE2.png"), calcNether},
	{"chunk", calcWikiIcon("Chunk.png"), calcChunk},
	{"distance", calcWikiIcon("Map.png"), calcDistance},
	{"stronghold", calcWikiIcon("Eye_of_Ender_JE2_BE2.png"), calcStronghold},
	{"tick", calcWikiIcon("Clock_JE2_BE2.png"), calcTick},
	{"exp", calcWikiIcon("Experience_Orb_Value_17-36.png"), calcExp},
	{"armor", calcWikiIcon("Iron_Chestplate_%28item%29_JE2_BE2.png"), calcArmor},
	{"stack", calcWikiIcon("Bundle_JE3_BE2.png"), calcStack},
	{"color", calcWikiIcon("Baroque_%28texture%29_JE1_BE1.png"), calcColor},
	{"uuid", calcWikiIcon("Steve_JE5.png"), calcUUID},
	{"seed", calcWikiIcon("Wheat_Seeds_JE1_BE1.png"), calcSeed},
}

func calcByID(id string) *calcItem {
	for i := range calcItems {
		if calcItems[i].ID == id {
			return &calcItems[i]
		}
	}
	return nil
}

func calcWikiIcon(name string) string {
	return "https://zh.minecraft.wiki/images/thumb/" + name + "/120px-" + name
}
