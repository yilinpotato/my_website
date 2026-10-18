from pathlib import Path
import datetime, shutil, subprocess
site=Path('/home/myl/my_website')
conf=site/'nginx_site_common.conf'
backup=Path('/home/myl/backups')/('study-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
backup.mkdir(parents=True)
shutil.copy2(conf,backup/conf.name)
text=conf.read_text()
if '# AI STUDY ASSISTANT' not in text:
 text+='''
    # AI STUDY ASSISTANT
    location = /ai { return 301 /ai/; }
    location /ai/api/ {
        proxy_pass http://127.0.0.1:5012/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 180;
        client_max_body_size 32M;
    }
    location /ai/assets/ {
        try_files $uri =404;
        expires 7d;
    }
    location /ai/ {
        try_files $uri $uri/ /ai/index.html;
        add_header Cache-Control "no-cache";
    }
'''
conf.write_text(text)
if subprocess.run(['nginx','-t']).returncode:
 shutil.copy2(backup/conf.name,conf)
 raise SystemExit('Restored nginx config')
subprocess.run(['systemctl','reload','nginx'],check=True)
print('Backup:',backup)
