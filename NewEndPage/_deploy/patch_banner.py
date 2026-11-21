import os
import shutil

NGINX_CONF = "/www/server/nginx/conf/nginx.conf"
VHOSTS = [
    "/www/server/panel/vhost/nginx/end.g-fish.dpdns.org.conf",
    "/www/server/panel/vhost/nginx/end.pcl.xn--9iq209asnop94b.top.conf",
]
BAK = ".bak2.20261121"

ADMIN_BLOCK = """    location ^~ /admin {
        proxy_pass http://$pcl_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Accept-Encoding "";
        proxy_read_timeout 120s;
        sub_filter '</body>' '$admin_banner</body>';
        sub_filter_once on;
        sub_filter_types text/html;
    }

"""


def patch_nginx_conf():
    s = open(NGINX_CONF, encoding="utf-8").read()
    if "$endpage_is_new" in s:
        print("nginx.conf already has map")
        return
    anchor = '    split_clients "${remote_addr}${request_id}${time_iso8601}" $pcl_upstream {\n'
    assert anchor in s, "split_clients block not found"
    end = s.index("}\n", s.index(anchor)) + 2
    block = (
        "\n"
        "    map $pcl_upstream $endpage_is_new {\n"
        '        "127.0.0.1:8778" "1";\n'
        '        default         "0";\n'
        "    }\n"
        "\n"
        "    map $pcl_upstream $admin_banner {\n"
        '        "127.0.0.1:8778" \'<div style="position:fixed;top:0;left:0;right:0;z-index:99999;background:#f9d423;color:#000;text-align:center;padding:6px 0;font:14px/1.4 sans-serif;">您现在访问的是新版测试站点</div>\';\n'
        "        default         '';\n"
        "    }\n"
    )
    if not os.path.exists(NGINX_CONF + BAK):
        shutil.copyfile(NGINX_CONF, NGINX_CONF + BAK)
    s = s[:end] + block + s[end:]
    open(NGINX_CONF, "w", encoding="utf-8").write(s)
    print("nginx.conf patched")


def patch_vhost(path):
    s = open(path, encoding="utf-8").read()
    orig = s
    hsts = 'add_header Strict-Transport-Security "max-age=31536000" always;'
    if "X-New-EndPage" not in s:
        assert hsts in s, path + ": HSTS line not found"
        s = s.replace(hsts, hsts + "\n    add_header X-New-EndPage $endpage_is_new always;", 1)
    # 先移除旧的 admin 块（可能在 80 端口的 server 里），再插到 8443 的 location / 前。
    if "location ^~ /admin" in s:
        start = s.index("    location ^~ /admin {")
        end = s.index("    location / {", start)
        s = s[:start] + s[end:]
    idx = s.index("listen 127.0.0.1:8443 ssl;")
    loc = s.index("    location / {", idx)
    s = s[:loc] + ADMIN_BLOCK + s[loc:]
    if s != orig:
        if not os.path.exists(path + BAK):
            shutil.copyfile(path, path + BAK)
        open(path, "w", encoding="utf-8").write(s)
        print(path, "patched")
    else:
        print(path, "unchanged")


patch_nginx_conf()
for p in VHOSTS:
    patch_vhost(p)
