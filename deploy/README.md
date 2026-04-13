# Деплой find-proxy-web

```bash
# 1. скопировать проект
sudo mkdir -p /opt/find-proxy-web
sudo rsync -a --exclude venv ./ /opt/find-proxy-web/
sudo chown -R www-data:www-data /opt/find-proxy-web

# 2. venv + зависимости
sudo -u www-data python3 -m venv /opt/find-proxy-web/venv
sudo -u www-data /opt/find-proxy-web/venv/bin/pip install -r /opt/find-proxy-web/requirements.txt

# 3. sudoers (обязательно через visudo-проверку)
sudo install -m 440 -o root -g root \
  /opt/find-proxy-web/deploy/sudoers.d-find-proxy \
  /etc/sudoers.d/find-proxy
sudo visudo -c

# 4. systemd
sudo install -m 644 \
  /opt/find-proxy-web/deploy/find-proxy-web.service \
  /etc/systemd/system/find-proxy-web.service
sudo systemctl daemon-reload
sudo systemctl enable --now find-proxy-web

# 5. проверка
sudo -u www-data sudo -n /usr/local/bin/find-proxy test
curl -s http://127.0.0.1:5000/ | head
journalctl -u find-proxy-web -f
```
