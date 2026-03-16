#!/bin/bash
# Deploy script - kills server, pulls code, restarts cleanly

echo "=== Stopping grbl_server ==="
pkill -9 -f grbl_server.py 2>/dev/null
sleep 1
fuser -k 8000/tcp 2>/dev/null
fuser -k 8001/tcp 2>/dev/null
echo "Waiting for ports to release..."
sleep 4

echo "=== Pulling latest code ==="
cd ~/grbl-server
git pull

echo "=== Starting grbl_server ==="
nohup python3 grbl_server.py > /tmp/grbl.log 2>&1 &
sleep 2

echo "=== Verifying ==="
if ps aux | grep -v grep | grep grbl_server.py > /dev/null; then
    PID=$(pgrep -f grbl_server.py)
    echo "Server running (PID: $PID)"
    
    # Test HTTP
    HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/ 2>/dev/null)
    if [ "$HTTP_CODE" = "200" ]; then
        echo "HTTP OK (port 8000)"
    else
        echo "HTTP status: $HTTP_CODE"
        tail -10 /tmp/grbl.log
    fi
else
    echo "Server FAILED to start"
    tail -20 /tmp/grbl.log
fi
