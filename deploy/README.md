# Deploy on Raspberry Pi

## 1 — Auto-start the app on boot (systemd)

```bash
sudo cp deploy/telegram-rag.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable telegram-rag
sudo systemctl start telegram-rag

# Check status
sudo systemctl status telegram-rag
journalctl -u telegram-rag -f
```

The app runs at **http://rpi.local:8080**.

## 2 — Landing page on port 80 (nginx)

```bash
sudo apt install nginx -y

# Copy the static landing page into nginx's web root.
# It must NOT be served from /home/user/... — nginx runs as www-data and
# cannot traverse /home/user (0700), so serving from there 404s with
# "Permission denied". /var/www is world-traversable and owned by www-data.
sudo mkdir -p /var/www/telegram-rag
sudo cp web/landing.html /var/www/telegram-rag/landing.html
sudo chown -R www-data:www-data /var/www/telegram-rag

sudo cp deploy/nginx-telegram-rag.conf /etc/nginx/sites-available/telegram-rag
sudo ln -s /etc/nginx/sites-available/telegram-rag /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default   # remove default page
sudo nginx -t                                  # verify config
sudo systemctl enable nginx
sudo systemctl restart nginx
```

The landing page is now at **http://rpi.local/** — it checks if the FastAPI service is up
and shows a link to open the app.

> **Editing the landing page later:** `web/landing.html` is the source of truth.
> After changing it, re-copy it: `sudo cp web/landing.html /var/www/telegram-rag/`.

## Service commands

```bash
sudo systemctl start   telegram-rag
sudo systemctl stop    telegram-rag
sudo systemctl restart telegram-rag
```
