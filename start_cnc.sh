#!/bin/bash
# Start GRBL server with camera stream

# Kill any existing processes
pkill -f grbl_server.py 2>/dev/null
pkill -f cncjs 2>/dev/null
pkill mjpg_streamer 2>/dev/null
sleep 1

# Start camera stream in background
export LD_LIBRARY_PATH=/usr/local/lib/mjpg-streamer:$LD_LIBRARY_PATH
nohup mjpg_streamer -i "input_uvc.so -d /dev/video0 -r 640x480 -f 15" -o "output_http.so -p 8080 -w /usr/local/share/mjpg-streamer/www" > /tmp/mjpg.log 2>&1 &
disown

# Start GRBL server
cd ~/grbl-server
nohup python3 grbl_server.py > /tmp/grbl.log 2>&1 &
disown

# Wait for server to start
sleep 2

# Open in browser
nohup x-www-browser "http://localhost:8000" > /dev/null 2>&1 &
disown

echo ""
echo "GRBL server started with camera"
echo ""
echo "Access from anywhere:"
echo "  Control Panel: http://192.168.68.105:8000"
echo "  Camera:        http://192.168.68.105:8080/?action=stream"
echo ""
echo "(Safe to close terminal)"
