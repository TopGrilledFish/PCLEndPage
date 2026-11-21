import shutil

p = "/www/server/nginx/conf/nginx.conf"
bak = p + ".bak.20261121-1825"
old = "include /www/server/panel/vhost/nginx/*.conf;"
block = (
    '    split_clients "${remote_addr}${request_id}${time_iso8601}" $pcl_upstream {\n'
    "        50% 127.0.0.1:8778;\n"
    "        *   127.0.0.1:8787;\n"
    "    }\n\n"
)
s = open(p, encoding="utf-8").read()
if "$pcl_upstream" in s:
    print("already patched")
else:
    assert old in s, "include line not found"
    shutil.copyfile(p, bak)
    s = s.replace(old, block + old, 1)
    open(p, "w", encoding="utf-8").write(s)
    print("patched nginx.conf, backup:", bak)
