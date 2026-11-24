package main

import (
	"net/http"
	"strings"
)

// isTrustedClient 判断请求是否来自 PCL 启动器或真实浏览器（对应旧版
// is_trusted_client）：UA 带 PCL2 / Mozilla，或 Referer 是 PCL 的帮助页。
// 纯脚本、扫描器会被拒绝页挡下。
//
// 注意：这里只区分「启动器/浏览器 vs 爬虫」，不区分 PCL2 与 PCL-CE——
// 两者的主页入口已经由 /Custom.xaml 与 /Custom-CE.xaml 两个 URL 决定，
// 同一台机器上同时开 PCL2 和 PCL-CE 也不会互相影响。
func isTrustedClient(r *http.Request) bool {
	ua := strings.ToLower(r.UserAgent())
	if strings.Contains(ua, "pcl2") || strings.Contains(ua, "mozilla") {
		return true
	}
	ref := strings.ToLower(r.Referer())
	return strings.Contains(ref, "pcl2.server") || strings.Contains(ref, "pcl2.open.server")
}
