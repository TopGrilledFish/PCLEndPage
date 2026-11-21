#!/bin/bash
IP=9.9.9.9
for u in /Custom.xaml /; do
  echo "== $u 未选"
  curl -s -o /tmp/o.txt -D /tmp/h.txt -w '%{http_code} %{size_download}\n' -A 'PCL2/2.8.0' -H "X-Real-IP: $IP" "https://end.g-fish.dpdns.org$u"
  grep -i 'x-new-endpage' /tmp/h.txt
done
echo "== _new"
curl -s -o /dev/null -w '%{http_code}\n' -A 'PCL2/2.8.0' -H "X-Real-IP: $IP" https://end.g-fish.dpdns.org/_new
echo "== 已选 /Custom.xaml"
curl -s -o /tmp/o2.txt -D /tmp/h2.txt -w '%{http_code} %{size_download}\n' -A 'PCL2/2.8.0' -H "X-Real-IP: $IP" https://end.g-fish.dpdns.org/Custom.xaml
grep -i 'x-new-endpage' /tmp/h2.txt
echo "banner_count=$(grep -c '新版测试站点' /tmp/o2.txt)"
echo "verify_page=$(grep -c '访问验证' /tmp/o2.txt)"
