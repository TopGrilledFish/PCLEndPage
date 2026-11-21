import json

NEW = "/root/NewEndPage/config.json"
OLD = "/root/PCLEndPage/config.json"

cfg = json.load(open(NEW, encoding="utf-8"))
try:
    old = json.load(open(OLD, encoding="utf-8"))
except Exception:
    old = {}

cfg.setdefault("admin_token", old.get("admin_token", ""))
cfg.setdefault("admin_totp_secret", "")
cfg.setdefault("admin_session_hours", 8)
cfg.setdefault("legacy_log_file", "/root/PCLEndPage/var/server.log")
cfg.setdefault("log_file", "/root/NewEndPage/var/server.log")
cfg.setdefault("store_file", "/root/PCLEndPage/var/store.json")
cfg.setdefault("site_name", old.get("site_name", "PCL2 主页"))
cfg.setdefault("enable_ai", True)
cfg.setdefault("guard_clients", True)

# admin_token 为空时以旧版为准
if not cfg.get("admin_token"):
    cfg["admin_token"] = old.get("admin_token", "")

json.dump(cfg, open(NEW, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("config ok, token_set=%s, keys=%d" % (bool(cfg.get("admin_token")), len(cfg)))
