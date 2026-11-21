#!/bin/sh
set -e
for f in /www/server/panel/vhost/nginx/end.g-fish.dpdns.org.conf /www/server/panel/vhost/nginx/end.pcl.xn--9iq209asnop94b.top.conf; do
  cp "$f" "$f.bak.20261121-1825"
  sed -i 's|proxy_pass http://127.0.0.1:8787;|proxy_pass http://$pcl_upstream;|' "$f"
done
nginx -t
nginx -s reload
echo RELOADED
