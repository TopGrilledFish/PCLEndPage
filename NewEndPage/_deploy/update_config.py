# -*- coding: utf-8 -*-
import json

p = "/root/NewEndPage/config.json"
cfg = json.load(open(p, encoding="utf-8"))
cfg["verify_enabled"] = True
cfg["verify_days"] = 7
cfg["verify_file"] = "/root/PCLEndPage/var/verify.json"
cfg["pref_file"] = "pref.json"
json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("config updated:", sorted(cfg.keys()))
