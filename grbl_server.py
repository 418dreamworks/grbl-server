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
import smtplib
from email.mime.text import MIMEText
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

# Start position enforcement (machine coordinates)
# Tool must be at bottom-right corner (rapid button ↘) before starting
# Bottom-right = MPos X near 0, MPos Y at far end, Z near top
START_POS_TOLERANCE = 5.0  # mm tolerance for position check
START_POS_MARGIN = 2.0     # matches MARGIN in jog.html

# SMS Notification via email-to-SMS gateway
SMS_ENABLED = True
SMS_SMTP_SERVER = 'smtp.gmail.com'
SMS_SMTP_PORT = 587
SMS_FROM_EMAIL = 'tzuohann@gmail.com'
SMS_APP_PASSWORD = 'hgtv igwu kmhu fdad'
SMS_TO_ADDRESS = '2674743645@tmomail.net'


def send_sms(message: str) -> bool:
    """Send SMS notification via email-to-SMS gateway."""
    if not SMS_ENABLED:
        return False

    try:
        msg = MIMEText(message)
        msg['From'] = SMS_FROM_EMAIL
        msg['To'] = SMS_TO_ADDRESS
        msg['Subject'] = ''  # SMS doesn't use subject

        with smtplib.SMTP(SMS_SMTP_SERVER, SMS_SMTP_PORT) as server:
            server.starttls()
            server.login(SMS_FROM_EMAIL, SMS_APP_PASSWORD)
            server.send_message(msg)

        print(f'[SMS] Sent: {message}')
        return True
    except Exception as e:
        print(f'[SMS] Failed to send: {e}')
        return False


async def send_sms_async(message: str) -> bool:
    """Async wrapper for send_sms."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, send_sms, message)

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

    async def send_nowait(self, line: str):
        """Send a G-code command without waiting for response (for jog commands)."""
        if not self.connected or not self.ser:
            return
        cmd = line.strip() + '\n'
        self.ser.write(cmd.encode('utf-8'))
        if self.logger:
            self.logger.log_send(line.strip())
        if self.broadcast_callback:
            await self.broadcast_callback({'type': 'serial_write', 'line': line.strip()})

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
        self.broadcast_callback = None
        self.stream_task: Optional[asyncio.Task] = None
        self.recovery_file: str = 'recovery.txt'

    def load_file(self, filename: str, content: str):
        """Load G-code file content."""
        self.filename = filename
        self.lines = [l.strip() for l in content.split('\n') if l.strip() and not l.strip().startswith(';')]
        self.total_lines = len(self.lines)
        self.current_line = 0
        print(f'[Streamer] Loaded {filename}: {self.total_lines} lines')

    async def start(self, from_line: int = 0, skip_position_check: bool = False):
        """Start streaming from specified line."""
        if not self.lines:
            print('[Streamer] No file loaded')
            return False, 'No file loaded'

        # Check start position (machine must be at bottom-right corner - rapid ↘ button)
        if not skip_position_check and from_line == 0:
            mpos = self.grbl.status.mpos

            # Get work area size from GRBL settings
            work_max_y = float(self.grbl.settings.get('$131', 400))

            # Expected position: bottom-right corner
            # MPos X = -MARGIN (near home X)
            # MPos Y = -(workMaxY - MARGIN) (far end of Y)
            # MPos Z = near 0 (top)
            expected_x = -START_POS_MARGIN
            expected_y = -(work_max_y - START_POS_MARGIN)
            expected_z = -START_POS_MARGIN

            dx = abs(mpos['x'] - expected_x)
            dy = abs(mpos['y'] - expected_y)
            dz = abs(mpos['z'] - expected_z)

            if dx > START_POS_TOLERANCE or dy > START_POS_TOLERANCE or dz > START_POS_TOLERANCE:
                msg = f'Start position check failed. Click ↘ rapid button first. ' \
                      f'Expected MPos near ({expected_x:.0f}, {expected_y:.0f}, {expected_z:.0f}), ' \
                      f'got ({mpos["x"]:.1f}, {mpos["y"]:.1f}, {mpos["z"]:.1f}).'
                print(f'[Streamer] {msg}')
                if self.broadcast_callback:
                    await self.broadcast_callback({
                        'type': 'file_error',
                        'line': 0,
                        'gcode': '',
                        'error': msg,
                    })
                return False, msg

        self.current_line = max(0, from_line)
        self.running = True
        self.paused = False
        self.stop_flag = False

        self.stream_task = asyncio.create_task(self._stream_loop())
        print(f'[Streamer] Started from line {self.current_line}')
        return True, 'Started'

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
                })

            self.current_line += 1

        # Done
        self.running = False
        self._save_recovery()

        if self.current_line >= self.total_lines and not self.stop_flag:
            print(f'[Streamer] Completed {self.filename}')

            # Return to home position (Z first, then XY)
            print('[Streamer] Returning to home position...')
            if self.broadcast_callback:
                await self.broadcast_callback({
                    'type': 'file_status',
                    'filename': self.filename,
                    'current': self.total_lines,
                    'total': self.total_lines,
                    'percent': 100,
                    'current_gcode': '; Returning to home...',
                })

            # Stop spindle first
            await self.grbl.send_command('M5')

            # Return to bottom-right corner (same as start position)
            work_max_y = float(self.grbl.settings.get('$131', 400))
            home_x = -START_POS_MARGIN
            home_y = -(work_max_y - START_POS_MARGIN)
            home_z = -START_POS_MARGIN

            # Z near top first
            await self.grbl.send_command(f'G53 G0 Z{home_z}')
            # Wait for Z move to complete
            while True:
                await asyncio.sleep(0.2)
                if self.grbl.status.state == 'Idle':
                    break
            # Then XY to bottom-right corner
            await self.grbl.send_command(f'G53 G0 X{home_x} Y{home_y}')
            # Wait for XY move
            while True:
                await asyncio.sleep(0.2)
                if self.grbl.status.state == 'Idle':
                    break

            print('[Streamer] At home position')
            if self.broadcast_callback:
                await self.broadcast_callback({
                    'type': 'file_done',
                    'filename': self.filename,
                    'total': self.total_lines,
                })

    def _save_recovery(self):
        """Save recovery information to file."""
        try:
            with open(self.recovery_file, 'w') as f:
                f.write(f'file={self.filename}\n')
                f.write(f'total={self.total_lines}\n')
                f.write(f'current={self.current_line}\n')
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

    def analyze(self) -> Dict[str, Any]:
        """Analyze loaded G-code for feed rates, plunge rates, and spindle speeds."""
        return analyze_gcode(self.lines)


def analyze_gcode(lines: List[str]) -> Dict[str, Any]:
    """
    Analyze G-code lines for key parameters and timing.

    Returns dict with:
    - max_feed: Maximum XY feed rate
    - max_plunge: Maximum plunge rate
    - min_spindle, max_spindle: Spindle speed range
    - tool_changes: Count of M6 commands
    - tool_change_lines: List of line numbers where M6 occurs (1-indexed)
    - cumulative_time: List of cumulative machining time (minutes) at each line
    - total_time: Total machining time in minutes
    """
    import math

    max_feed = 0.0
    max_plunge = 0.0
    min_spindle = float('inf')
    max_spindle = 0.0
    tool_changes = 0
    tool_change_lines = []

    # Position tracking
    pos_x, pos_y, pos_z = 0.0, 0.0, 0.0
    current_f = 1000.0  # Default feed rate
    last_z = 0.0
    is_g1_mode = False  # Track if we're in G1 mode

    # Bounds tracking (work coordinates - assumes starting at origin)
    min_x, max_x = 0.0, 0.0
    min_y, max_y = 0.0, 0.0
    min_z, max_z = 0.0, 0.0

    # Time tracking - cumulative time at each line
    cumulative_time = []
    total_time = 0.0

    # Regex patterns
    f_pattern = re.compile(r'F([\d.]+)', re.IGNORECASE)
    x_pattern = re.compile(r'X([-\d.]+)', re.IGNORECASE)
    y_pattern = re.compile(r'Y([-\d.]+)', re.IGNORECASE)
    z_pattern = re.compile(r'Z([-\d.]+)', re.IGNORECASE)
    s_pattern = re.compile(r'S([\d.]+)', re.IGNORECASE)

    for line_idx, line in enumerate(lines):
        upper = line.upper().strip()

        # Skip comments
        if upper.startswith(';') or upper.startswith('('):
            cumulative_time.append(total_time)
            continue

        # Track modal G-code state
        if 'G0' in upper or 'G00' in upper:
            is_g1_mode = False
        if 'G1' in upper or 'G01' in upper:
            is_g1_mode = True

        # Extract F value if present
        f_match = f_pattern.search(line)
        if f_match:
            current_f = float(f_match.group(1))

        # Extract target positions
        new_x, new_y, new_z = pos_x, pos_y, pos_z
        x_match = x_pattern.search(line)
        y_match = y_pattern.search(line)
        z_match = z_pattern.search(line)
        if x_match:
            new_x = float(x_match.group(1))
        if y_match:
            new_y = float(y_match.group(1))
        if z_match:
            last_z = pos_z
            new_z = float(z_match.group(1))

        # Extract S value if present
        s_match = s_pattern.search(line)
        if s_match:
            s_val = float(s_match.group(1))
            if s_val > 0:
                min_spindle = min(min_spindle, s_val)
                max_spindle = max(max_spindle, s_val)

        # Calculate time for G1 moves (G0 rapids are assumed instant for estimation)
        if is_g1_mode or 'G1' in upper or 'G01' in upper:
            # Calculate 3D distance
            dx = new_x - pos_x
            dy = new_y - pos_y
            dz = new_z - pos_z
            distance = math.sqrt(dx*dx + dy*dy + dz*dz)

            # Time = distance / feed_rate (feed is mm/min, so time is in minutes)
            if current_f > 0 and distance > 0:
                move_time = distance / current_f
                total_time += move_time

            # Track max feed/plunge
            if z_match and new_z < last_z:
                max_plunge = max(max_plunge, current_f)
            else:
                max_feed = max(max_feed, current_f)

        # Update position and bounds
        pos_x, pos_y, pos_z = new_x, new_y, new_z
        min_x, max_x = min(min_x, pos_x), max(max_x, pos_x)
        min_y, max_y = min(min_y, pos_y), max(max_y, pos_y)
        min_z, max_z = min(min_z, pos_z), max(max_z, pos_z)

        # Store cumulative time at this line
        cumulative_time.append(total_time)

        # Count tool changes
        if 'M6' in upper or 'M06' in upper:
            tool_changes += 1
            tool_change_lines.append(line_idx + 1)

    # Handle case where no spindle commands found
    if min_spindle == float('inf'):
        min_spindle = 0

    # Compute time to next tool change for each line (reverse cumulative)
    # time_to_next_tc[i] = time from line i to the next M6 (or end of file)
    time_to_next_tc = []
    tc_times = [cumulative_time[ln - 1] if ln - 1 < len(cumulative_time) else total_time
                for ln in tool_change_lines]
    tc_times.append(total_time)  # End of file as final "tool change"

    tc_idx = 0
    for i, ct in enumerate(cumulative_time):
        line_num = i + 1
        # Move to next tool change if we've passed the current one
        while tc_idx < len(tool_change_lines) and line_num > tool_change_lines[tc_idx]:
            tc_idx += 1
        # Time remaining to next tool change
        next_tc_time = tc_times[tc_idx] if tc_idx < len(tc_times) else total_time
        time_to_next_tc.append(next_tc_time - ct)

    return {
        'max_feed': max_feed,
        'max_plunge': max_plunge,
        'min_spindle': min_spindle,
        'max_spindle': max_spindle,
        'tool_changes': tool_changes,
        'tool_change_lines': tool_change_lines,
        'time_to_next_tc': time_to_next_tc,
        'total_time': total_time,
        'bounds': {
            'min_x': min_x, 'max_x': max_x,
            'min_y': min_y, 'max_y': max_y,
            'min_z': min_z, 'max_z': max_z,
        },
    }


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
        self.macros.notify_callback = self.send_notification
        self.macros.streamer = self.streamer  # Give macros access to loaded G-code

    async def send_notification(self, message: str):
        """Send SMS notification for user action required."""
        await send_sms_async(message)

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
            nowait = msg.get('nowait', False)
            if nowait:
                await self.grbl.send_nowait(line)
            else:
                result = await self.grbl.send_command(line)
                await ws.send(json.dumps({'type': 'response', 'to': line, 'result': result}))

        elif msg_type == 'realtime':
            byte = msg.get('byte', 0)
            if isinstance(byte, int):
                print(f'[WS] Realtime command: 0x{byte:02X}')
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
            analysis = self.streamer.analyze()
            await ws.send(json.dumps({
                'type': 'file_status',
                'filename': filename,
                'current': 0,
                'total': self.streamer.total_lines,
                'percent': 0,
                'analysis': analysis,
            }))

        elif msg_type == 'file_start':
            from_line = msg.get('from_line', 0)
            skip_check = msg.get('skip_position_check', False)
            # Skip position check if resuming from middle of file
            if from_line > 0:
                skip_check = True
            success, error_msg = await self.streamer.start(from_line, skip_position_check=skip_check)
            if not success:
                await ws.send(json.dumps({'type': 'file_start_error', 'error': error_msg}))

        elif msg_type == 'file_pause':
            self.streamer.pause()

        elif msg_type == 'file_resume':
            self.streamer.resume()

        elif msg_type == 'file_stop':
            self.streamer.stop()

        elif msg_type == 'macro_run':
            name = msg.get('name', '')
            tool_dia = msg.get('tool_diameter', 6.35)  # Default to 1/4"
            edge_sign = msg.get('edge_sign', 0)  # -1=left/front, +1=right/back
            # Map button names to macro file names
            name_map = {
                'set_z': 'tool_measure',
                'tool_change': 'tool_change',
            }
            macro_name = name_map.get(name, name)
            asyncio.create_task(self.macros.run_macro(macro_name, tool_diameter=tool_dia, edge_sign=edge_sign))

        elif msg_type == 'macro_continue':
            self.macros.continue_macro()

        elif msg_type == 'macro_cancel':
            self.macros.cancel()

        elif msg_type == 'macro_list':
            # Return list of available macros grouped by category
            macros_dir = Path(__file__).parent / 'macros'
            macros = []

            # Add config.py as first item
            config_path = Path(__file__).parent / 'config.py'
            if config_path.exists():
                macros.append({
                    'name': '_config',
                    'label': 'Config',
                    'category': '0_Config'  # 0_ prefix to sort first
                })

            if macros_dir.exists():
                for f in sorted(macros_dir.glob('*.py')):
                    name = f.stem
                    # Convert filename to label: probe_z -> Probe:Z
                    parts = name.split('_', 1)
                    if len(parts) == 2:
                        category = parts[0].capitalize()
                        label = parts[1].replace('_', ' ').title().replace(' ', '')
                        display_label = f'{category}:{label}'
                    else:
                        display_label = name.capitalize()
                    macros.append({
                        'name': name,
                        'label': display_label,
                        'category': parts[0].capitalize() if len(parts) == 2 else 'Other'
                    })
            await ws.send(json.dumps({'type': 'macro_list', 'macros': macros}))

        elif msg_type == 'macro_load':
            name = msg.get('name', '')
            # Special handling for config.py
            if name == '_config':
                macro_path = Path(__file__).parent / 'config.py'
            else:
                macro_path = Path(__file__).parent / 'macros' / f'{name}.py'
            if macro_path.exists():
                code = macro_path.read_text()
                await ws.send(json.dumps({'type': 'macro_content', 'name': name, 'code': code}))
            else:
                await ws.send(json.dumps({'type': 'macro_content', 'name': name, 'code': '', 'error': 'File not found'}))

        elif msg_type == 'macro_save':
            name = msg.get('name', '')
            code = msg.get('code', '')
            # Special handling for config.py
            if name == '_config':
                macro_path = Path(__file__).parent / 'config.py'
                display_name = 'config'
            else:
                macro_path = Path(__file__).parent / 'macros' / f'{name}.py'
                display_name = name
            macro_path.write_text(code)
            await self.broadcast({'type': 'macro_log', 'name': name, 'message': f'Saved {display_name}.py'})

        elif msg_type == 'fixture_list':
            # Return current fixtures list
            await ws.send(json.dumps({
                'type': 'fixtures',
                'fixtures': self.macros.fixtures
            }))

        elif msg_type == 'fixture_remove':
            # Remove fixture by index
            index = msg.get('index', -1)
            if 0 <= index < len(self.macros.fixtures):
                removed = self.macros.fixtures.pop(index)
                await self.broadcast({
                    'type': 'fixtures',
                    'fixtures': self.macros.fixtures
                })
                await self.broadcast({
                    'type': 'macro_log',
                    'name': 'fixtures',
                    'message': f'Removed fixture #{index + 1} at X{removed["x"]:.1f} Y{removed["y"]:.1f}'
                })

        elif msg_type == 'fixture_clear':
            # Clear all fixtures
            self.macros.fixtures.clear()
            await self.broadcast({
                'type': 'fixtures',
                'fixtures': []
            })
            await self.broadcast({
                'type': 'macro_log',
                'name': 'fixtures',
                'message': 'All fixtures cleared'
            })

        elif msg_type == 'check_collisions':
            # Check loaded G-code against fixtures
            collisions = self.macros.check_collisions()
            await ws.send(json.dumps({
                'type': 'collision_check',
                'collisions': collisions,
                'count': len(collisions)
            }))
            if collisions:
                await self.broadcast({
                    'type': 'macro_log',
                    'name': 'collision_check',
                    'message': f'WARNING: {len(collisions)} potential fixture collisions detected!'
                })

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

        # Enable SO_REUSEADDR to allow quick restart
        class ReusableTCPServer(socketserver.TCPServer):
            allow_reuse_address = True

        with ReusableTCPServer(('0.0.0.0', self.http_port), Handler) as httpd:
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
