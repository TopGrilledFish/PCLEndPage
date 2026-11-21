package main

import "time"

// lunarMonth 农历月（含闰月）。
type lunarMonth struct {
	month int
	leap  bool
	days  int
}

func (l *LunarService) yearDays(year int) int {
	info := l.cal.LunarInfo[year-1900]
	sum := 348
	for bit := 0x8000; bit > 0x8; bit >>= 1 {
		if info&bit != 0 {
			sum++
		}
	}
	return sum + l.leapDays(year)
}

func (l *LunarService) leapMonth(year int) int {
	return l.cal.LunarInfo[year-1900] & 0xF
}

func (l *LunarService) leapDays(year int) int {
	if l.leapMonth(year) == 0 {
		return 0
	}
	if l.cal.LunarInfo[year-1900]&0x10000 != 0 {
		return 30
	}
	return 29
}

func (l *LunarService) monthDays(year, month int) int {
	if l.cal.LunarInfo[year-1900]&(0x10000>>uint(month)) != 0 {
		return 30
	}
	return 29
}

func (l *LunarService) months(year int) []lunarMonth {
	leap := l.leapMonth(year)
	out := make([]lunarMonth, 0, 13)
	for m := 1; m <= 12; m++ {
		out = append(out, lunarMonth{month: m, days: l.monthDays(year, m)})
		if leap == m {
			out = append(out, lunarMonth{month: m, leap: true, days: l.leapDays(year)})
		}
	}
	return out
}

// solarToLunarFull 公历 → 农历（农历年、月、日、是否闰月）。
func (l *LunarService) solarToLunarFull(target time.Time) (int, int, int, bool) {
	if len(l.cal.LunarInfo) < 201 {
		return 0, 0, 0, false
	}
	epoch := time.Date(1900, 1, 31, 0, 0, 0, 0, time.UTC)
	offset := int(target.Sub(epoch).Hours() / 24)
	year := 1900
	for year < 2100 && offset >= l.yearDays(year) {
		offset -= l.yearDays(year)
		year++
	}
	for _, mm := range l.months(year) {
		if offset < mm.days {
			return year, mm.month, offset + 1, mm.leap
		}
		offset -= mm.days
	}
	return year, 12, 30, false
}

func (l *LunarService) solarToLunar(target time.Time) (int, int, bool) {
	_, m, d, leap := l.solarToLunarFull(target)
	return m, d, leap
}

// lunarToSolar 农历（非闰月）→ 公历。
func (l *LunarService) lunarToSolar(year, month, day int) time.Time {
	total := 0
	for y := 1900; y < year; y++ {
		total += l.yearDays(y)
	}
	for _, mm := range l.months(year) {
		if mm.month == month && !mm.leap {
			break
		}
		total += mm.days
	}
	return time.Date(1900, 3, 2, 0, 0, 0, 0, time.UTC).AddDate(0, 0, total+day-31)
}
