package main

import (
	"encoding/json"
	"os"
	"time"
)

// verifyOK 读取旧版 PCLEndPage 的访问验证记录，判断该 IP 是否已通过人机验证。
// 旧版格式：{"ips": {"1.2.3.4": 过期时间戳}, "fails": {"1.2.3.4": [失败次数, 解封时间]}}
// 新版入口复用旧版验证（验证页、图片、答案校验都在旧版），这里只读结果。
func (s *Server) verifyOK(ip string) bool {
	if !s.cfg.VerifyEnabled {
		return true
	}
	if ip == "" {
		return false
	}
	raw, err := os.ReadFile(s.cfg.VerifyFile)
	if err != nil {
		return false
	}
	var data struct {
		Ips map[string]json.Number `json:"ips"`
	}
	if err := json.Unmarshal(raw, &data); err != nil {
		return false
	}
	v, ok := data.Ips[ip]
	if !ok {
		return false
	}
	exp, err := v.Float64()
	if err != nil {
		return false
	}
	return exp > float64(time.Now().Unix())
}
