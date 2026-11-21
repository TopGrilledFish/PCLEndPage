package main

import "embed"

//go:embed assets
var assetsFS embed.FS

func mustReadAsset(name string) string {
	data, err := assetsFS.ReadFile(name)
	if err != nil {
		panic("内置资源缺失：" + name)
	}
	return string(data)
}
