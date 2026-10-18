$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$port = 4175
$url = "http://127.0.0.1:$port/"
$listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue

if (-not $listener) {
  Start-Process -FilePath "python" -ArgumentList "-m", "http.server", $port, "--directory", $root -WindowStyle Hidden
  Start-Sleep -Milliseconds 700
}

Start-Process $url
