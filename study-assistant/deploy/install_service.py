from pathlib import Path
import subprocess, shutil, datetime, os
root=Path('/home/myl/apps/study-assistant')
data=Path('/home/myl/.local/share/study-assistant')
data.mkdir(parents=True,exist_ok=True)
(root/'production.env').write_text('DATABASE_URL=sqlite:////home/myl/.local/share/study-assistant/study.db\nERROR_BOOK_UPLOAD_DIR=/home/myl/.local/share/study-assistant/uploads\nWHISPER_MODEL=/home/myl/apps/study-assistant/models/whisper-base\nHF_ENDPOINT=https://hf-mirror.com\nOMP_NUM_THREADS=2\nOPENBLAS_NUM_THREADS=1\n')
os.chmod(root/'production.env',0o600)
service='''[Unit]
Description=Potatoma AI Study Assistant
After=network-online.target
Wants=network-online.target

[Service]
User=myl
Group=myl
WorkingDirectory=/home/myl/apps/study-assistant/backend
EnvironmentFile=/home/myl/my_website/.env
EnvironmentFile=/home/myl/apps/study-assistant/production.env
ExecStart=/home/myl/apps/study-assistant/venv/bin/gunicorn --bind 127.0.0.1:5012 --workers 1 --threads 2 --timeout 180 wsgi:app
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/home/myl/apps/study-assistant /home/myl/.local/share/study-assistant
MemoryMax=1600M
CPUQuota=150%

[Install]
WantedBy=multi-user.target
'''
Path('/etc/systemd/system/potatoma-study.service').write_text(service)
subprocess.run(['chown','-R','myl:myl',str(root),str(data)],check=True)
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','enable','--now','potatoma-study'],check=True)
print('Service installed')
