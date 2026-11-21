# -*- coding: utf-8 -*-
"""取消 nginx 50% 随机分流：入口全部交给 Go（8778），由 Go 按 IP 偏好决定新旧。"""
import re
import shutil

NGINX = "/www/server/nginx/conf/nginx.conf"
VHOSTS = [
    "/www/server/panel/vhost/nginx/end.g-fish.dpdns.org.conf",
    "/www/server/panel/vhost/nginx/end.pcl.xn--9iq209asnop94b.top.conf",
]
BACKUP_SUFFIX = ".bak3.20261121"


def backup(path):
    shutil.copy2(path, path + BACKUP_SUFFIX)


def fix_nginx_conf(path):
    s = open(path, encoding="utf-8").read()
    orig = s
    s = re.sub(r"\n\s*split_clients[^{]*\{[^}]*\}\n", "\n", s)
    s = re.sub(r"\n\s*map \$pcl_upstream \$endpage_is_new \{[^}]*\}\n", "\n", s)
    s = re.sub(r"\n\s*map \$pcl_upstream \$admin_banner \{[^}]*\}\n", "\n", s)
    if s != orig:
        backup(path)
        open(path, "w", encoding="utf-8").write(s)
        print("nginx.conf 已更新（删除 split_clients 与 map）")
    else:
        print("nginx.conf 无需修改")


def fix_vhost(path):
    s = open(path, encoding="utf-8").read()
    orig = s
    s = s.replace("add_header X-New-EndPage $endpage_is_new always;\n", "")
    s = s.replace("proxy_pass http://$pcl_upstream;", "proxy_pass http://127.0.0.1:8778;")
    s = re.sub(r"\n\s*sub_filter '</body>' '\$admin_banner</body>';", "", s)
    s = re.sub(r"\n\s*sub_filter_once on;", "", s)
    s = re.sub(r"\n\s*sub_filter_types text/html;", "", s)
    s = re.sub(r'\n\s*proxy_set_header Accept-Encoding "";', "", s)
    if s != orig:
        backup(path)
        open(path, "w", encoding="utf-8").write(s)
        print("vhost 已更新:", path)
    else:
        print("vhost 无需修改:", path)


fix_nginx_conf(NGINX)
for v in VHOSTS:
    fix_vhost(v)
