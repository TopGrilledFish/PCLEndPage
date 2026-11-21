import json
import os

DIR = "/root/NewEndPage"
os.makedirs(DIR + "/var", exist_ok=True)

old = json.load(open("/root/PCLEndPage/config.json", encoding="utf-8"))
keys = old.get("uapi_keys") or old.get("uapi_key") or ""
if isinstance(keys, str):
    keys = [k.strip() for k in keys.split(",") if k.strip()]

cfg = {
    "host": "127.0.0.1",
    "port": 8778,
    "base_url": "https://end.g-fish.dpdns.org",
    "version": "229df76",
    "legacy_backend": "127.0.0.1:8787",
    "uapi_keys": keys,
    "enable_wallpaper": True,
    "enable_saying": True,
    "enable_lunar": True,
    "enable_weather": True,
    "enable_geo": True,
    "weather_city": "",
    "weather_api": "https://uapis.cn/api/v1/misc/weather",
    "lunar_api": "https://uapis.cn/api/v1/misc/lunartime",
    "sayings_api": "https://uapis.cn/api/v1/saying/random",
    "http_timeout": 8,
    "geo_timeout": 4,
    "geo_cache_hours": 24,
    "weather_cache_seconds": 10800,
    "trust_proxy_headers": True,
    "max_retries": 2,
}
with open(DIR + "/config.json", "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
print("config written, uapi keys:", len(keys))
