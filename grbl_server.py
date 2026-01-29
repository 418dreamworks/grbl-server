#!/usr/bin/env python3
"""
GRBL WebSocket Server - CNCjs Replacement

Single Python server that:
1. Owns the serial port with DTR-safe connection
2. Serves jog.html over HTTP at /
3. Provides WebSocket API at /ws for real-time control
4. Handles file streaming with recovery tracking
5. Runs SetZ and ToolChange macros

Dependencies: pip install pyserial websockets

Usage: python3 grbl_server.py [--port 8000] [--serial /dev/ttyACM0]
"""

import asyncio
import json
import re
import time
import os
import argparse
from pathlib import Path
from typing import Optional, Dict, Any, List, Set
from dataclasses import dataclass, field
from enum import Enum

import serial
import serial.tools.list_ports
import websockets
from websockets.http11 import Response
from http import HTTPStatus
import http.server
import socketserver
import threading

from macros import MacroEngine

# ============================================================
# CONFIGURATION
# ============================================================

DEFAULT_HTTP_PORT = 8000
DEFAULT_SERIAL_PORT = '/dev/ttyACM0'
DEFAULT_BAUD_RATE = 115200
STATUS_POLL_INTERVAL = 0.2  # 200ms
RECOVERY_SAVE_INTERVAL = 100  # Save recovery every N lines
LOG_DIR = 'logs'
LOG_MAX_AGE_DAYS = 7

# ============================================================
# SERIAL LOGGER
# ============================================================

class SerialLogger:
    """Logs all serial communication to daily text files with auto-cleanup."""

    def __init__(self, log_dir: str = LOG_DIR):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.current_file: Optional[Path] = None
        self.current_date: str = ''
        self._cleanup_old_logs()

    def _cleanup_old_logs(self):
        """Remove log files older than LOG_MAX_AGE_DAYS."""
        cutoff = time.time() - (LOG_MAX_AGE_DAYS * 24 * 60 * 60)
        removed = 0
        for log_file in self.log_dir.glob('*.log'):
            if log_file.stat().st_mtime < cutoff:
                log_file.unlink()
                removed += 1
        if removed:
            print(f'[Logger] Cleaned up {removed} old log files')

    def _get_log_file(self) -> Path:
        """Get current day's log file, creating new one if date changed."""
        today = time.strftime('%Y-%m-%d')
        if today != self.current_date:
            self.current_date = today
            self.current_file = self.log_dir / f'grbl_{today}.log'
        return self.current_file

    def log_receive(self, line: str):
        """Log data received from GRBL."""
        self._write(f'< {line}')

    def log_send(self, line: str):
        """Log data sent to GRBL."""
        self._write(f'> {line}')

    def log_realtime(self, byte_val: int):
        """Log real-time command sent."""
        # Map common real-time commands to readable names
        names = {0x18: 'RESET', ord('?'): 'STATUS', ord('!'): 'HOLD', ord('~'): 'RESUME'}
        name = names.get(byte_val, f'0x{byte_val:02X}')
        self._write(f'>RT {name}')

    def _write(self, msg: str):
        """Write timestamped message to log file."""
        timestamp = time.strftime('%H:%M:%S.') + f'{int(time.time() * 1000) % 1000:03d}'
        log_file = self._get_log_file()
        try:
            with open(log_file, 'a') as f:
                f.write(f'{timestamp} {msg}\n')
        except Exception as e:
            print(f'[Logger] Write error: {e}')

# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class MachineStatus:
    state: str = 'Unknown'
    mpos: Dict[str, float] = field(default_factory=lambda: {'x': 0, 'y': 0, 'z': 0, 'a': 0})
    wpos: Dict[str, float] = field(default_factory=lambda: {'x': 0, 'y': 0, 'z': 0, 'a': 0})
    wco: Dict[str, float] = field(default_factory=lambda: {'x': 0, 'y': 0, 'z': 0, 'a': 0})
    feed_override: int = 100
    spindle_override: int = 100
    feed_rate: float = 0
    spindle_speed: float = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            'type': 'status',
            'state': self.state,
            'mpos': self.mpos,
            'wpos': self.wpos,
            'feed_override': self.feed_override,
            'spindle_override': self.spindle_override,
            'feed_rate': self.feed_rate,
            'spindle_speed': self.spindle_speed,
        }

# ============================================================
# GRBL CONNECTION
# ============================================================

class GrblConnection:
    """Manages serial connection to GRBL controller with DTR-safe handling."""

    def __init__(self, logger: Optional[SerialLogger] = None):
        self.ser: Optional[serial.Serial] = None
        self.port: str = ''
        self.connected: bool = False
        self.status: MachineStatus = MachineStatus()
        self.settings: Dict[str, str] = {}
        self.response_queue: asyncio.Queue = asyncio.Queue()
        self.read_task: Optional[asyncio.Task] = None
        self.poll_task: Optional[asyncio.Task] = None
        self.broadcast_callback = None
        self.wco_cached: Dict[str, float] = {'x': 0, 'y': 0, 'z': 0, 'a': 0}
        self.g28_pos: Dict[str, float] = {'x': 0, 'y': 0, 'z': 0, 'a': 0}
        self.logger = logger

    async def connect(self, port: str, baud: int = DEFAULT_BAUD_RATE) -> bool:
        """Connect to serial port using DTR-safe method."""
        if self.connected:
            await self.disconnect()

        try:
            # DTR-safe serial open (from send_gcode2.py)
            self.ser = serial.Serial()
            self.ser.port = port
            self.ser.baudrate = baud
            self.ser.timeout = 0.1
            self.ser.dsrdtr = False  # Disable DTR/DSR flow control
            self.ser.open()
            self.ser.dtr = False     # Explicitly hold DTR low after opening

            self.port = port
            self.connected = True

            # Start read loop and status polling
            self.read_task = asyncio.create_task(self._read_loop())
            self.poll_task = asyncio.create_task(self._poll_status())

            # Request settings and stored positions
            await self.send_command('$$')
            await self.send_command('$#')

            print(f'[GRBL] Connected to {port}')
            return True

        except Exception as e:
            print(f'[GRBL] Connection failed: {e}')
            self.connected = False
            return False

    async def disconnect(self):
        """Disconnect from serial port."""
        self.connected = False

        if self.read_task:
            self.read_task.cancel()
            try:
                await self.read_task
            except asyncio.CancelledError:
                pass
            self.read_task = None

        if self.poll_task:
            self.poll_task.cancel()
            try:
                await self.poll_task
            except asyncio.CancelledError:
                pass
            self.poll_task = None

        if self.ser and self.ser.is_open:
            self.ser.close()
        self.ser = None
        self.port = ''
        print('[GRBL] Disconnected')

    async def _read_loop(self):
        """Async loop to read serial data."""
        loop = asyncio.get_event_loop()
        buffer = ''

        while self.connected and self.ser:
            try:
                # Non-blocking read in executor
                data = await loop.run_in_executor(None, lambda: self.ser.read(256) if self.ser else b'')
                if data:
                    buffer += data.decode('utf-8', errors='ignore')

                    # Process complete lines
                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        line = line.strip()
                        if line:
                            await self._handle_line(line)
                else:
                    await asyncio.sleep(0.01)

            except Exception as e:
                if self.connected:
                    print(f'[GRBL] Read error: {e}')
                await asyncio.sleep(0.1)

    async def _handle_line(self, line: str):
        """Process a line received from GRBL."""
        # Log to file
        if self.logger:
            self.logger.log_receive(line)

        # Broadcast raw serial data
        if self.broadcast_callback:
            await self.broadcast_callback({'type': 'serial_read', 'line': line})

        # Status response: <Idle|MPos:0.000,0.000,0.000,0.000|...>
        if line.startswith('<') and line.endswith('>'):
            self._parse_status(line)
            if self.broadcast_callback:
                await self.broadcast_callback(self.status.to_dict())
            return

        # OK response
        if line == 'ok':
            await self.response_queue.put(('ok', line))
            return

        # Error response
        if line.startswith('error:'):
            await self.response_queue.put(('error', line))
            if self.broadcast_callback:
                await self.broadcast_callback({'type': 'response', 'result': line})
            return

        # Alarm
        if line.startswith('ALARM:'):
            code = line.split(':')[1] if ':' in line else '?'
            self.status.state = 'Alarm'
            if self.broadcast_callback:
                await self.broadcast_callback({'type': 'alarm', 'code': code})
            return

        # Probe result: [PRB:x,y,z,a:1]
        if line.startswith('[PRB:'):
            self._parse_probe(line)
            return

        # G28 stored position: [G28:x,y,z,a]
        if line.startswith('[G28:'):
            coords = line[5:-1].split(',')
            self.g28_pos = {
                'x': float(coords[0]) if len(coords) > 0 else 0,
                'y': float(coords[1]) if len(coords) > 1 else 0,
                'z': float(coords[2]) if len(coords) > 2 else 0,
                'a': float(coords[3]) if len(coords) > 3 else 0,
            }
            print(f'[GRBL] G28 position: X{self.g28_pos["x"]} Y{self.g28_pos["y"]} Z{self.g28_pos["z"]}')
            return

        # Settings: $N=value (already broadcast via serial_read above)
        if line.startswith('$') and '=' in line:
            key, value = line.split('=', 1)
            self.settings[key] = value
            return

        # Grbl startup
        if 'Grbl' in line:
            print(f'[GRBL] Controller: {line}')
            return

    def _parse_status(self, line: str):
        """Parse GRBL status response."""
        # Remove < >
        content = line[1:-1]
        parts = content.split('|')

        if parts:
            self.status.state = parts[0]

        for part in parts[1:]:
            if part.startswith('MPos:'):
                coords = part[5:].split(',')
                self.status.mpos = {
                    'x': float(coords[0]) if len(coords) > 0 else 0,
                    'y': float(coords[1]) if len(coords) > 1 else 0,
                    'z': float(coords[2]) if len(coords) > 2 else 0,
                    'a': float(coords[3]) if len(coords) > 3 else 0,
                }
                # Compute work position from cached WCO
                self.status.wpos = {
                    'x': self.status.mpos['x'] - self.wco_cached['x'],
                    'y': self.status.mpos['y'] - self.wco_cached['y'],
                    'z': self.status.mpos['z'] - self.wco_cached['z'],
                    'a': self.status.mpos['a'] - self.wco_cached['a'],
                }

            elif part.startswith('WCO:'):
                # Work Coordinate Offset (sent periodically, cache it)
                coords = part[4:].split(',')
                self.wco_cached = {
                    'x': float(coords[0]) if len(coords) > 0 else 0,
                    'y': float(coords[1]) if len(coords) > 1 else 0,
                    'z': float(coords[2]) if len(coords) > 2 else 0,
                    'a': float(coords[3]) if len(coords) > 3 else 0,
                }
                # Recompute wpos
                self.status.wpos = {
                    'x': self.status.mpos['x'] - self.wco_cached['x'],
                    'y': self.status.mpos['y'] - self.wco_cached['y'],
                    'z': self.status.mpos['z'] - self.wco_cached['z'],
                    'a': self.status.mpos['a'] - self.wco_cached['a'],
                }

            elif part.startswith('Ov:'):
                # Overrides: feed,rapid,spindle
                overrides = part[3:].split(',')
                self.status.feed_override = int(overrides[0]) if len(overrides) > 0 else 100
                # overrides[1] is rapid override (not used much)
                self.status.spindle_override = int(overrides[2]) if len(overrides) > 2 else 100

            elif part.startswith('FS:') or part.startswith('F:'):
                # Feed and Speed: FS:feed,speed or F:feed
                if part.startswith('FS:'):
                    fs = part[3:].split(',')
                    self.status.feed_rate = float(fs[0]) if len(fs) > 0 else 0
                    self.status.spindle_speed = float(fs[1]) if len(fs) > 1 else 0
                else:
                    self.status.feed_rate = float(part[2:])

    def _parse_probe(self, line: str):
        """Parse probe result."""
        # [PRB:x,y,z,a:1] - 1 means success
        match = re.match(r'\[PRB:([^:]+):(\d)\]', line)
        if match:
            coords = match.group(1).split(',')
            success = match.group(2) == '1'
            if self.broadcast_callback:
                asyncio.create_task(self.broadcast_callback({
                    'type': 'probe',
                    'success': success,
                    'x': float(coords[0]) if len(coords) > 0 else 0,
                    'y': float(coords[1]) if len(coords) > 1 else 0,
                    'z': float(coords[2]) if len(coords) > 2 else 0,
                    'a': float(coords[3]) if len(coords) > 3 else 0,
                }))

    async def _poll_status(self):
        """Periodically send status query."""
        while self.connected:
            try:
                self.send_realtime(b'?')
                await asyncio.sleep(STATUS_POLL_INTERVAL)
            except Exception as e:
                if self.connected:
                    print(f'[GRBL] Poll error: {e}')
                await asyncio.sleep(1)

    async def send_command(self, line: str, timeout: float = 10.0) -> str:
        """Send a G-code command and wait for ok/error response."""
        if not self.connected or not self.ser:
            return 'error:not_connected'

        # Clear any pending responses
        while not self.response_queue.empty():
            try:
                self.response_queue.get_nowait()
            except:
                break

        # Send command
        cmd = line.strip() + '\n'
        self.ser.write(cmd.encode('utf-8'))

        # Log to file
        if self.logger:
            self.logger.log_send(line.strip())

        if self.broadcast_callback:
            await self.broadcast_callback({'type': 'serial_write', 'line': line.strip()})

        # Wait for response
        try:
            result_type, result = await asyncio.wait_for(
                self.response_queue.get(),
                timeout=timeout
            )
            return result
        except asyncio.TimeoutError:
            return 'error:timeout'

    def send_realtime(self, data: bytes):
        """Send real-time command (no newline, no response expected)."""
        if self.connected and self.ser:
            self.ser.write(data)
            # Log to file (except status polls to avoid spam)
            if self.logger and data != b'?':
                self.logger.log_realtime(data[0] if data else 0)

# ============================================================
# FILE STREAMER
# ============================================================

class FileStreamer:
    """Handles G-code file streaming with recovery tracking."""

    def __init__(self, grbl: GrblConnection):
        self.grbl = grbl
        self.filename: str = ''
        self.lines: List[str] = []
        self.total_lines: int = 0
        self.current_line: int = 0
        self.running: bool = False
        self.paused: bool = False
        self.stop_flag: bool = False
        self.last_safe_line: int = 0
        self.last_safe_gcode: str = ''
        self.broadcast_callback = None
        self.stream_task: Optional[asyncio.Task] = None
        self.recovery_file: str = 'recovery.txt'

    def load_file(self, filename: str, content: str):
        """Load G-code file content."""
        self.filename = filename
        self.lines = [l.strip() for l in content.split('\n') if l.strip() and not l.strip().startswith(';')]
        self.total_lines = len(self.lines)
        self.current_line = 0
        self.last_safe_line = 0
        self.last_safe_gcode = ''
        print(f'[Streamer] Loaded {filename}: {self.total_lines} lines')

    async def start(self, from_line: int = 0):
        """Start streaming from specified line."""
        if not self.lines:
            print('[Streamer] No file loaded')
            return

        self.current_line = max(0, from_line)
        self.running = True
        self.paused = False
        self.stop_flag = False

        self.stream_task = asyncio.create_task(self._stream_loop())
        print(f'[Streamer] Started from line {self.current_line}')

    async def _stream_loop(self):
        """Main streaming loop."""
        while self.running and self.current_line < self.total_lines:
            if self.stop_flag:
                break

            if self.paused:
                await asyncio.sleep(0.1)
                continue

            line = self.lines[self.current_line]

            # Send command and wait for response
            result = await self.grbl.send_command(line)

            if result.startswith('error'):
                # Report error but continue (or stop based on error type)
                if self.broadcast_callback:
                    await self.broadcast_callback({
                        'type': 'file_error',
                        'line': self.current_line + 1,
                        'gcode': line,
                        'error': result,
                    })
                # For now, continue on errors (can make this configurable)

            # Track safe lines (G0/G1 without I/J parameters)
            if self._is_safe_line(line):
                self.last_safe_line = self.current_line + 1
                self.last_safe_gcode = line

            # Save recovery periodically
            if self.current_line % RECOVERY_SAVE_INTERVAL == 0:
                self._save_recovery()

            # Broadcast progress
            if self.broadcast_callback:
                await self.broadcast_callback({
                    'type': 'file_status',
                    'filename': self.filename,
                    'current': self.current_line + 1,
                    'total': self.total_lines,
                    'percent': (self.current_line + 1) / self.total_lines * 100,
                    'current_gcode': line,
                    'last_safe_line': self.last_safe_line,
                    'last_safe_gcode': self.last_safe_gcode,
                })

            self.current_line += 1

        # Done
        self.running = False
        self._save_recovery()

        if self.current_line >= self.total_lines:
            print(f'[Streamer] Completed {self.filename}')
            if self.broadcast_callback:
                await self.broadcast_callback({
                    'type': 'file_done',
                    'filename': self.filename,
                    'total': self.total_lines,
                })

    def _is_safe_line(self, line: str) -> bool:
        """Check if line is a safe resumption point (G0/G1 without I/J)."""
        upper = line.upper()
        # Must be a motion command
        if not (upper.startswith('G0') or upper.startswith('G1')):
            if not ('G0 ' in upper or 'G1 ' in upper or 'G00' in upper or 'G01' in upper):
                return False
        # Must not have I/J parameters (arc center offsets)
        if ' I' in upper or ' J' in upper:
            return False
        return True

    def _save_recovery(self):
        """Save recovery information to file."""
        try:
            with open(self.recovery_file, 'w') as f:
                f.write(f'file={self.filename}\n')
                f.write(f'total={self.total_lines}\n')
                f.write(f'current={self.current_line}\n')
                f.write(f'safe_line={self.last_safe_line}\n')
                f.write(f'safe_gcode={self.last_safe_gcode}\n')
                f.write(f'timestamp={time.strftime("%Y-%m-%d %H:%M:%S")}\n')
                if self.grbl.connected:
                    f.write(f'mpos_z={self.grbl.status.mpos["z"]:.3f}\n')
        except Exception as e:
            print(f'[Streamer] Recovery save failed: {e}')

    def pause(self):
        """Pause streaming."""
        self.paused = True
        print('[Streamer] Paused')

    def resume(self):
        """Resume streaming."""
        self.paused = False
        print('[Streamer] Resumed')

    def stop(self):
        """Stop streaming."""
        self.stop_flag = True
        self.running = False
        self.paused = False
        self._save_recovery()
        print('[Streamer] Stopped')

# ============================================================
# WEBSOCKET SERVER
# ============================================================

class GrblServer:
    """WebSocket server for CNC control."""

    def __init__(self, http_port: int, serial_port: str):
        self.http_port = http_port
        self.ws_port = http_port + 1  # WebSocket on separate port
        self.serial_port = serial_port
        self.logger = SerialLogger()
        self.grbl = GrblConnection(logger=self.logger)
        self.streamer = FileStreamer(self.grbl)
        self.macros = MacroEngine(self.grbl)
        self.clients: Set = set()
        self.html_content: str = ''

        # Set up broadcast callbacks
        self.grbl.broadcast_callback = self.broadcast
        self.streamer.broadcast_callback = self.broadcast
        self.macros.broadcast_callback = self.broadcast

    async def broadcast(self, msg: Dict[str, Any]):
        """Broadcast message to all connected clients."""
        if not self.clients:
            return
        data = json.dumps(msg)
        await asyncio.gather(
            *[client.send(data) for client in self.clients],
            return_exceptions=True
        )

    async def handle_client(self, websocket):
        """Handle WebSocket client connection."""
        self.clients.add(websocket)
        print(f'[WS] Client connected ({len(self.clients)} total)')

        # Send current connection status to new client
        if self.grbl.connected:
            await websocket.send(json.dumps({
                'type': 'connected',
                'port': self.grbl.port
            }))

        try:
            async for message in websocket:
                try:
                    msg = json.loads(message)
                    await self.handle_message(websocket, msg)
                except json.JSONDecodeError:
                    await websocket.send(json.dumps({'type': 'error', 'message': 'Invalid JSON'}))
        finally:
            self.clients.discard(websocket)
            print(f'[WS] Client disconnected ({len(self.clients)} total)')

    async def handle_message(self, ws, msg: Dict[str, Any]):
        """Route incoming WebSocket message."""
        msg_type = msg.get('type', '')

        if msg_type == 'connect':
            port = msg.get('port', self.serial_port)
            success = await self.grbl.connect(port)
            await ws.send(json.dumps({
                'type': 'connected' if success else 'error',
                'port': port,
            }))

        elif msg_type == 'disconnect':
            await self.grbl.disconnect()
            await ws.send(json.dumps({'type': 'disconnected'}))

        elif msg_type == 'list_ports':
            ports = [p.device for p in serial.tools.list_ports.comports()]
            await ws.send(json.dumps({'type': 'ports', 'ports': ports}))

        elif msg_type == 'gcode':
            line = msg.get('line', '')
            result = await self.grbl.send_command(line)
            await ws.send(json.dumps({'type': 'response', 'to': line, 'result': result}))

        elif msg_type == 'realtime':
            byte = msg.get('byte', 0)
            if isinstance(byte, int):
                self.grbl.send_realtime(bytes([byte]))

        elif msg_type == 'unlock':
            await self.grbl.send_command('$X')

        elif msg_type == 'reset':
            self.grbl.send_realtime(b'\x18')

        elif msg_type == 'feed_hold':
            self.grbl.send_realtime(b'!')

        elif msg_type == 'cycle_start':
            self.grbl.send_realtime(b'~')

        elif msg_type == 'settings':
            await ws.send(json.dumps({'type': 'settings', 'settings': self.grbl.settings}))

        elif msg_type == 'file_upload':
            filename = msg.get('filename', 'unknown.nc')
            content = msg.get('content', '')
            self.streamer.load_file(filename, content)
            await ws.send(json.dumps({
                'type': 'file_status',
                'filename': filename,
                'current': 0,
                'total': self.streamer.total_lines,
                'percent': 0,
            }))

        elif msg_type == 'file_start':
            from_line = msg.get('from_line', 0)
            await self.streamer.start(from_line)

        elif msg_type == 'file_pause':
            self.streamer.pause()

        elif msg_type == 'file_resume':
            self.streamer.resume()

        elif msg_type == 'file_stop':
            self.streamer.stop()

        elif msg_type == 'macro_run':
            name = msg.get('name', '')
            if name == 'set_z':
                asyncio.create_task(self.macros.run_set_z())
            elif name == 'tool_change':
                asyncio.create_task(self.macros.run_tool_change())

        elif msg_type == 'macro_continue':
            self.macros.continue_macro()

        elif msg_type == 'macro_cancel':
            self.macros.cancel()

    def load_html(self):
        """Load jog.html from same directory as script."""
        script_dir = Path(__file__).parent
        html_path = script_dir / 'jog.html'
        if html_path.exists():
            # Update WebSocket URL in HTML to use ws_port
            content = html_path.read_text()
            # Replace the getWsUrl function to use the correct WS port
            self.html_content = content.replace(
                "return 'ws://' + window.location.host + '/ws';",
                f"return 'ws://' + window.location.hostname + ':{self.ws_port}';"
            )
            print(f'[Server] Loaded {html_path}')
        else:
            self.html_content = '<html><body><h1>jog.html not found</h1></body></html>'
            print(f'[Server] WARNING: {html_path} not found')

    def run_http_server(self):
        """Run HTTP server in a thread."""
        script_dir = Path(__file__).parent

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(handler_self, *args, **kwargs):
                super().__init__(*args, directory=str(script_dir), **kwargs)

            def do_GET(handler_self):
                if handler_self.path == '/' or handler_self.path == '/index.html':
                    handler_self.send_response(200)
                    handler_self.send_header('Content-type', 'text/html')
                    handler_self.end_headers()
                    handler_self.wfile.write(self.html_content.encode())
                else:
                    super().do_GET()

            def log_message(handler_self, format, *args):
                pass  # Suppress HTTP logs

        with socketserver.TCPServer(('0.0.0.0', self.http_port), Handler) as httpd:
            print(f'[HTTP] Serving on http://0.0.0.0:{self.http_port}')
            httpd.serve_forever()

    async def start(self):
        """Start the server."""
        self.load_html()

        # Start HTTP server in background thread
        http_thread = threading.Thread(target=self.run_http_server, daemon=True)
        http_thread.start()

        # Auto-connect to serial port
        if os.path.exists(self.serial_port):
            await self.grbl.connect(self.serial_port)

        # Start WebSocket server
        async with websockets.serve(
            self.handle_client,
            '0.0.0.0',
            self.ws_port,
        ):
            print(f'[WS] WebSocket server on ws://0.0.0.0:{self.ws_port}')
            await asyncio.Future()  # Run forever

# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='GRBL WebSocket Server')
    parser.add_argument('--port', type=int, default=DEFAULT_HTTP_PORT, help='HTTP/WS port')
    parser.add_argument('--serial', default=DEFAULT_SERIAL_PORT, help='Serial port')
    args = parser.parse_args()

    server = GrblServer(args.port, args.serial)

    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        print('\n[Server] Shutting down...')

if __name__ == '__main__':
    main()
