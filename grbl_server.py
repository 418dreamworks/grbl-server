#!/usr/bin/env python3
VERSION = '1.205'
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
import collections
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

import logging

from macros import MacroEngine

# Error log file - persistent across restarts
_error_logger = logging.getLogger('cnc_errors')
_error_logger.setLevel(logging.DEBUG)
_err_handler = logging.FileHandler('error.log')
_err_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
_error_logger.addHandler(_err_handler)

def elog(msg):
    """Log to error.log file."""
    _error_logger.info(msg)
    print(f'[LOG] {msg}')

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
    pins: str = ''

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
            'pins': self.pins,
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
        self.stream_queue: asyncio.Queue = asyncio.Queue()  # for character-counting streamer
        self.streaming: bool = False  # when True, ok/error go to stream_queue
        self.read_task: Optional[asyncio.Task] = None
        self.poll_task: Optional[asyncio.Task] = None
        self.broadcast_callback = None
        self.wco_cached: Dict[str, float] = {'x': 0, 'y': 0, 'z': 0, 'a': 0}
        self.g28_pos: Dict[str, float] = {'x': 0, 'y': 0, 'z': 0, 'a': 0}
        self.last_probe: Dict[str, Any] = {'x': 0, 'y': 0, 'z': 0, 'a': 0, 'success': False}
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

            elog(f'GRBL connected to {port}')
            return True

        except Exception as e:
            elog(f'GRBL connect failed: {e}')
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
            if self.streaming:
                await self.stream_queue.put(('ok', line))
            else:
                await self.response_queue.put(('ok', line))
            return

        # Error response
        if line.startswith('error:'):
            elog(f'GRBL error: {line}')
            if self.streaming:
                await self.stream_queue.put(('error', line))
            else:
                await self.response_queue.put(('error', line))
            if self.broadcast_callback:
                await self.broadcast_callback({'type': 'response', 'result': line})
            return

        # Alarm
        if line.startswith('ALARM:'):
            code = line.split(':')[1] if ':' in line else '?'
            elog(f'ALARM: {code}')
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
            elog(f'G28 pos: X{self.g28_pos["x"]} Y{self.g28_pos["y"]} Z{self.g28_pos["z"]}')
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
            new_state = parts[0]
            if new_state != self.status.state:
                elog(f'State: {self.status.state} -> {new_state}')
            self.status.state = new_state

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
                new_wco = {
                    'x': float(coords[0]) if len(coords) > 0 else 0,
                    'y': float(coords[1]) if len(coords) > 1 else 0,
                    'z': float(coords[2]) if len(coords) > 2 else 0,
                    'a': float(coords[3]) if len(coords) > 3 else 0,
                }
                if new_wco != self.wco_cached:
                    elog(f'WCO changed: X{new_wco["x"]:.3f} Y{new_wco["y"]:.3f} Z{new_wco["z"]:.3f} A{new_wco.get("a", 0):.3f}')
                self.wco_cached = new_wco
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

            elif part.startswith('Pn:'):
                # Input pins: X=limit, Y=limit, Z=limit, P=probe, etc.
                self.status.pins = part[3:]

    def _parse_probe(self, line: str):
        """Parse probe result."""
        # [PRB:x,y,z,a:1] - 1 means success
        match = re.match(r'\[PRB:([^:]+):(\d)\]', line)
        if match:
            coords = match.group(1).split(',')
            success = match.group(2) == '1'

            # Store for macro access
            self.last_probe = {
                'success': success,
                'x': float(coords[0]) if len(coords) > 0 else 0,
                'y': float(coords[1]) if len(coords) > 1 else 0,
                'z': float(coords[2]) if len(coords) > 2 else 0,
                'a': float(coords[3]) if len(coords) > 3 else 0,
            }

            if self.broadcast_callback:
                asyncio.create_task(self.broadcast_callback({
                    'type': 'probe',
                    **self.last_probe
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
            # Re-read stored positions after G28.1/G30.1 or G10 (WCO change)
            if line.strip().upper() in ('G28.1', 'G30.1') or line.strip().upper().startswith('G10'):
                await asyncio.sleep(0.1)
                await self.send_command('$#')
            # After $ setting change: soft-reset GRBL, re-read all settings, broadcast
            if line.strip().startswith('$') and '=' in line:
                await asyncio.sleep(0.2)
                self.send_realtime(b'\x18')  # Soft reset to flush EEPROM
                await asyncio.sleep(1.0)
                await self.send_command('$$')  # Re-read all settings from GRBL
                if self.broadcast_callback:
                    await self.broadcast_callback({'type': 'settings', 'settings': self.settings})
            return result
        except asyncio.TimeoutError:
            return 'error:timeout'

    def send_stream_line(self, line: str) -> int:
        """Send a G-code line for streaming (no wait). Returns bytes sent."""
        cmd = line.strip() + '\n'
        self.ser.write(cmd.encode('utf-8'))
        if self.logger:
            self.logger.log_send(line.strip())
        return len(cmd)

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
        self.air_cut: bool = False
        self.macros = None  # set by CNCServer after init
        self.continue_event: asyncio.Event = asyncio.Event()
        self.dist_mode: str = 'G90'  # track absolute vs relative
        self.z_margin: float = 2.0   # mm margin from machine limits

    def load_file(self, filename: str, content: str):
        """Load G-code file content."""
        self.filename = filename
        self.lines = [l.strip() for l in content.split('\n')]
        self.total_lines = len(self.lines)
        self.current_line = 0
        print(f'[Streamer] Loaded {filename}: {self.total_lines} lines')

    async def start(self, from_line: int = 0, skip_position_check: bool = False, air_cut: bool = False):
        """Start streaming from specified line."""
        self.air_cut = air_cut
        if self.macros:
            self.macros.air_cut = air_cut
        if not self.lines:
            print('[Streamer] No file loaded')
            return False, 'No file loaded'

        # Check start position (machine must be at bottom-right corner - rapid ↘ button)
        if not skip_position_check and from_line <= 1:
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

            bad = []
            if dx > START_POS_TOLERANCE:
                bad.append(f'X off by {dx:.1f}mm (at {mpos["x"]:.1f}, expected {expected_x:.0f})')
            if dy > START_POS_TOLERANCE:
                bad.append(f'Y off by {dy:.1f}mm (at {mpos["y"]:.1f}, expected {expected_y:.0f})')
            if dz > START_POS_TOLERANCE:
                bad.append(f'Z off by {dz:.1f}mm (at {mpos["z"]:.1f}, expected {expected_z:.0f})')

            if bad:
                msg = f'Start position check failed. Click ↘ rapid button first. {"; ".join(bad)}'
                elog(f'STREAMER: {msg}')
                if self.broadcast_callback:
                    await self.broadcast_callback({
                        'type': 'file_error',
                        'line': 0,
                        'gcode': '',
                        'error': msg,
                    })
                return False, msg

        # Check if file has tool changes and SetZ has been run
        if self.macros:
            has_tc = any(self._is_tool_change(l) for l in self.lines[max(0, from_line - 1):])
            if has_tc and (not self.macros.set_z_done or self.macros.probe_work_z is None):
                msg = 'File has tool changes — run Measure (SetZ) first.'
                elog(f'STREAMER: {msg}')
                if self.broadcast_callback:
                    await self.broadcast_callback({
                        'type': 'file_start_error',
                        'error': msg,
                    })
                return False, msg

        self.current_line = max(0, from_line - 1)  # Convert 1-indexed UI line to 0-indexed array
        self.running = True
        self.paused = False
        self.stop_flag = False

        # Clear stale state and set modal preamble when resuming mid-file
        if from_line > 1:
            # Unlock if in alarm
            if self.grbl.status.state == 'Alarm':
                await self.grbl.send_command('$X')
                await asyncio.sleep(0.5)
            # Resume from hold if needed
            if 'Hold' in self.grbl.status.state:
                self.grbl.send_realtime(b'~')
                await asyncio.sleep(0.5)
            # Drain stale queues
            for q in (self.grbl.response_queue, self.grbl.stream_queue):
                while not q.empty():
                    try:
                        q.get_nowait()
                    except:
                        break
            # Set modal state — run through air cut filter
            preamble = self._build_preamble(from_line)
            if preamble:
                for cmd in preamble:
                    filtered = self._prepare_line(cmd)
                    if filtered is None:
                        elog(f'STREAMER: Preamble (skipped by air cut): {cmd}')
                        continue
                    elog(f'STREAMER: Preamble: {filtered}')
                    await self.grbl.send_command(filtered)

        self.stream_task = asyncio.create_task(self._stream_loop())
        elog(f'STREAMER: Started {self.filename} from line {self.current_line}/{self.total_lines}')
        return True, 'Started'

    def _build_preamble(self, from_line: int) -> list:
        """Scan lines 0..from_line to extract modal state and position for mid-file resume.
        Returns G-code commands to restore state, move to position, and plunge to Z."""
        dist_mode = 'G90'
        plane = 'G17'
        feed = None
        spindle = None
        spindle_mode = None  # M3, M4, or M5
        coord_sys = 'G54'
        motion_mode = 'G0'
        x = None
        y = None
        z = None
        a = None

        for i in range(min(from_line - 1, len(self.lines))):
            upper = self.lines[i].upper().strip()
            if not upper or upper.startswith('(') or upper.startswith('%'):
                continue
            # Skip tracking position for G28 moves (machine coordinates)
            if 'G28' in upper:
                continue
            if 'G90' in upper and 'G91' not in upper:
                dist_mode = 'G90'
            elif 'G91' in upper:
                dist_mode = 'G91'
            if 'G17' in upper:
                plane = 'G17'
            elif 'G18' in upper:
                plane = 'G18'
            elif 'G19' in upper:
                plane = 'G19'
            for cs in ('G54', 'G55', 'G56', 'G57', 'G58', 'G59'):
                if cs in upper:
                    coord_sys = cs
            m = re.search(r'F([\d.]+)', upper)
            if m:
                feed = f'F{m.group(1)}'
            m = re.search(r'S(\d+)', upper)
            if m:
                spindle = m.group(1)
            for gm in ('G0', 'G1', 'G2', 'G3'):
                if re.search(r'\b' + gm + r'\b', upper):
                    motion_mode = gm
            if 'M3' in upper:
                spindle_mode = 'M3'
            elif 'M4' in upper:
                spindle_mode = 'M4'
            elif 'M5' in upper:
                spindle_mode = 'M5'
            # Track absolute positions (only in G90 mode)
            if dist_mode == 'G90':
                m = re.search(r'X([-\d.]+)', upper)
                if m:
                    x = float(m.group(1))
                m = re.search(r'Y([-\d.]+)', upper)
                if m:
                    y = float(m.group(1))
                m = re.search(r'Z([-\d.]+)', upper)
                if m:
                    z = float(m.group(1))
                m = re.search(r'A([-\d.]+)', upper)
                if m:
                    a = float(m.group(1))

        cmds = []
        # 1. Set modal state
        cmds.append(f'G90 {plane}')
        cmds.append(coord_sys)
        # 2. Spindle on (before moving so it's up to speed)
        if spindle_mode in ('M3', 'M4') and spindle:
            cmds.append(f'{spindle_mode} S{spindle}')
        # 3. Set feed rate
        if feed:
            cmds.append(f'G1 {feed}')
        # 4. Move to position (rapid, safe at current Z)
        if a is not None:
            cmds.append(f'G0 A{a:.3f}')
        xy_parts = []
        if x is not None:
            xy_parts.append(f'X{x:.3f}')
        if y is not None:
            xy_parts.append(f'Y{y:.3f}')
        if xy_parts:
            cmds.append(f'G0 {" ".join(xy_parts)}')
        # 5. Plunge to Z (rapid to last known Z)
        if z is not None:
            cmds.append(f'G0 Z{z:.3f}')
        # 6. Restore motion mode (only G1 — G2/G3 need arc params)
        if motion_mode == 'G1' and feed:
            cmds.append(f'G1 {feed}')
        # 7. Restore distance mode if it was G91
        if dist_mode == 'G91':
            cmds.append('G91')
        return cmds

    def continue_stream(self):
        """Signal continue after Z clamp pause."""
        self.continue_event.set()

    def _is_tool_change(self, line: str) -> bool:
        """Check if line is a tool change command (T## M6 or M6)."""
        upper = line.upper().strip()
        return 'M6' in upper or 'M06' in upper

    def _track_dist_mode(self, line: str):
        """Track G90/G91 distance mode from G-code lines."""
        upper = line.upper()
        if 'G90' in upper and 'G91' not in upper:
            self.dist_mode = 'G90'
        elif 'G91' in upper:
            self.dist_mode = 'G91'

    def _check_z_limit(self, line: str) -> tuple:
        """Check if Z move would exceed machine travel.
        Returns (is_safe, clamped_line, message).
        Only checks absolute mode, skips G28/G53 lines."""
        upper = line.upper().strip()
        # Skip G28, G53 (machine coords), and relative mode
        if 'G28' in upper or 'G53' in upper or self.dist_mode == 'G91':
            return True, line, ''
        z_match = re.search(r'Z([-\d.]+)', line, re.IGNORECASE)
        if not z_match:
            return True, line, ''

        wpos_z = float(z_match.group(1))
        wco_z = self.grbl.wco_cached.get('z', 0)
        mpos_z = wpos_z + wco_z
        max_travel_z = float(self.grbl.settings.get('$132', 200))

        clamped = False
        if mpos_z > -self.z_margin:  # too high (toward home)
            new_wpos_z = -self.z_margin - wco_z
            msg = f'Z{wpos_z:.3f} -> MPos Z{mpos_z:.1f} exceeds top limit. Clamp to Z{new_wpos_z:.3f}?'
            clamped = True
        elif mpos_z < -(max_travel_z - self.z_margin):  # too low
            new_wpos_z = -(max_travel_z - self.z_margin) - wco_z
            msg = f'Z{wpos_z:.3f} -> MPos Z{mpos_z:.1f} exceeds bottom limit. Clamp to Z{new_wpos_z:.3f}?'
            clamped = True

        if clamped:
            new_line = re.sub(r'Z[-\d.]+', f'Z{new_wpos_z:.3f}', line, count=1, flags=re.IGNORECASE)
            return False, new_line, msg
        return True, line, ''

    def _prepare_line(self, line: str) -> Optional[str]:
        """Apply transforms. G28 replaced with G53 G0 XYZ (no A). Air-cut strips Z/spindle."""
        # Replace G28 with G53 G0 for only the specified axes (no A)
        upper = line.upper().strip()
        if 'G28' in upper and 'G28.1' not in upper and 'G28.3' not in upper:
            g28 = self.grbl.g28_pos
            parts = []
            if 'X' in upper:
                parts.append(f'X{g28["x"]:.3f}')
            if 'Y' in upper:
                parts.append(f'Y{g28["y"]:.3f}')
            if 'Z' in upper:
                parts.append(f'Z{g28["z"]:.3f}')
            if parts:
                return 'G53 G0 ' + ' '.join(parts)
            return None
        if self.air_cut:
            upper = line.upper().strip()
            # Skip spindle/coolant
            if any(c in upper for c in ('M3', 'M4', 'M5', 'M7', 'M8', 'M9')) or upper.startswith('S'):
                return None
            # Skip G19/G18 plane arcs (YZ/XZ — meaningless without Z)
            if 'G19' in upper or 'G18' in upper:
                return None
            # Strip Z, K (helical arc component)
            line = re.sub(r'Z[-\d.]+', '', line, flags=re.IGNORECASE)
            line = re.sub(r'K[-\d.]+', '', line, flags=re.IGNORECASE)
            # Strip F from G1 only (arcs need feed rate), convert G1 to G0
            if re.search(r'\bG0*1\b', line):
                line = re.sub(r'F[\d.]+', '', line, flags=re.IGNORECASE)
                line = re.sub(r'\bG0*1\b', 'G0', line)
            # Set arc feed to max rate so arcs run fast
            elif re.search(r'\bG0*[23]\b', line):
                line = re.sub(r'F[\d.]+', '', line, flags=re.IGNORECASE)
                line = line.strip() + ' F5000'
            line = line.strip()
            # Skip if nothing useful left
            if not line or line == 'G0' or line == 'G17':
                return None
        return line

    async def _drain_buffer(self, sent_lines, buf_used_ref: list, timeout: float = 30.0):
        """Drain all pending stream responses. buf_used_ref is a single-element list [buf_used]."""
        while sent_lines and not self.stop_flag:
            try:
                result_type, result = await asyncio.wait_for(
                    self.grbl.stream_queue.get(), timeout=timeout
                )
            except asyncio.TimeoutError:
                elog('STREAMER: Timeout draining buffer')
                break
            nbytes, gcode, line_num = sent_lines.popleft()
            buf_used_ref[0] -= nbytes
            self.current_line = line_num + 1

    async def _wait_idle(self, max_polls: int = 100, interval: float = 0.2):
        """Poll until GRBL reaches Idle state."""
        for _ in range(max_polls):
            if self.grbl.status.state == 'Idle':
                return
            await asyncio.sleep(interval)

    async def _stream_loop(self):
        """Main streaming loop using character-counting protocol."""
        RX_BUF_SIZE = 128
        buf_used = 0
        # sent_lines tracks (byte_count, gcode, line_number) for each unacknowledged line
        sent_lines = collections.deque()
        send_idx = self.current_line  # next line to send
        z_clamp_approved = False  # once user approves, auto-clamp all subsequent

        # Enable streaming mode on grbl connection
        # Drain any stale responses
        while not self.grbl.stream_queue.empty():
            try:
                self.grbl.stream_queue.get_nowait()
            except:
                break
        self.grbl.streaming = True

        try:
            while self.running and (send_idx < self.total_lines or sent_lines):
                if self.stop_flag:
                    break

                if self.paused:
                    await asyncio.sleep(0.1)
                    continue

                # --- SEND: fill GRBL buffer ---
                while send_idx < self.total_lines and not self.stop_flag:
                    raw = self.lines[send_idx]

                    # Skip empty lines and comments
                    if not raw or raw.startswith(';') or raw.startswith('('):
                        send_idx += 1
                        self.current_line = send_idx
                        continue

                    # Tool change: drain buffer, run macro, then continue
                    if self._is_tool_change(raw) and self.macros:
                        buf_used_ref = [buf_used]
                        await self._drain_buffer(sent_lines, buf_used_ref)
                        buf_used = buf_used_ref[0]
                        await self._wait_idle()
                        # Exit streaming mode so macro can use send_command
                        self.grbl.streaming = False
                        elog(f'STREAMER: Tool change at line {send_idx + 1}: {raw}')
                        await self.macros.run_tool_change()
                        # Wait for macro to finish
                        while self.macros.running:
                            await asyncio.sleep(0.1)
                        # If tool change failed, stop streaming
                        if self.macros.last_error:
                            elog(f'STREAMER: Tool change failed: {self.macros.last_error}')
                            self.stop_flag = True
                            break
                        await self._wait_idle(max_polls=150)
                        # Refresh WCO after tool change (tool offset changes Z WCO)
                        await self.grbl.send_command('$#')
                        # Drain both queues before re-entering streaming
                        for q in (self.grbl.stream_queue, self.grbl.response_queue):
                            while not q.empty():
                                try:
                                    q.get_nowait()
                                except:
                                    break
                        self.grbl.streaming = True
                        buf_used = 0
                        send_idx += 1
                        self.current_line = send_idx
                        elog(f'STREAMER: Resuming from line {send_idx + 1}')
                        continue

                    line = self._prepare_line(raw)
                    if line is None:
                        send_idx += 1
                        self.current_line = send_idx
                        continue

                    # Track distance mode
                    self._track_dist_mode(line)

                    # Z safety check
                    is_safe, clamped_line, z_msg = self._check_z_limit(line)
                    if not is_safe:
                        if not z_clamp_approved:
                            # First occurrence: drain buffer and ask user
                            while sent_lines and not self.stop_flag:
                                try:
                                    rt, rv = await asyncio.wait_for(
                                        self.grbl.stream_queue.get(), timeout=30.0
                                    )
                                except asyncio.TimeoutError:
                                    break
                                nb, gc, ln = sent_lines.popleft()
                                buf_used -= nb
                                self.current_line = ln + 1
                            for _ in range(100):
                                if self.grbl.status.state == 'Idle':
                                    break
                                await asyncio.sleep(0.2)
                            elog(f'STREAMER: Z LIMIT line {send_idx + 1}: {z_msg}')
                            if self.broadcast_callback:
                                await self.broadcast_callback({
                                    'type': 'macro_status',
                                    'name': 'z_clamp',
                                    'step': 1, 'total': 1,
                                    'description': f'Line {send_idx + 1}: {z_msg} Press CONTINUE to auto-clamp all.',
                                    'command': line.strip(),
                                    'waiting': True,
                                })
                            self.continue_event.clear()
                            await self.continue_event.wait()
                            if self.stop_flag:
                                break
                            z_clamp_approved = True
                        line = clamped_line
                        elog(f'STREAMER: Z clamped line {send_idx + 1}')

                    cmd_len = len(line.strip() + '\n')
                    if buf_used + cmd_len > RX_BUF_SIZE:
                        break  # buffer full, wait for responses
                    nbytes = self.grbl.send_stream_line(line)
                    buf_used += nbytes
                    sent_lines.append((nbytes, line, send_idx))
                    send_idx += 1

                # --- RECEIVE: process one response ---
                if sent_lines:
                    while not self.stop_flag:
                        try:
                            result_type, result = await asyncio.wait_for(
                                self.grbl.stream_queue.get(), timeout=5.0
                            )
                            break  # got a response
                        except asyncio.TimeoutError:
                            state = self.grbl.status.state
                            # Keep waiting if machine is busy, holding, or paused
                            if state in ('Run', 'Jog') or 'Hold' in state or 'Door' in state:
                                continue
                            elog(f'STREAMER: Timeout waiting for response (state={state})')
                            self.stop_flag = True
                            break
                    if self.stop_flag:
                        break

                    nbytes, gcode, line_num = sent_lines.popleft()
                    buf_used -= nbytes
                    self.current_line = line_num + 1

                    # Broadcast buffer contents
                    if self.broadcast_callback:
                        buf_cmds = [g for _, g, _ in sent_lines]
                        await self.broadcast_callback({
                            'type': 'grbl_buffer',
                            'commands': buf_cmds,
                            'bytes': buf_used,
                            'max': 128,
                        })

                    if result.startswith('error'):
                        elog(f'GCODE ERROR line {line_num + 1}: {gcode} -> {result}')
                        if self.broadcast_callback:
                            await self.broadcast_callback({
                                'type': 'file_error',
                                'line': line_num + 1,
                                'gcode': gcode,
                                'error': result,
                            })

                    # Save recovery periodically
                    if line_num % RECOVERY_SAVE_INTERVAL == 0:
                        self._save_recovery()

                    # Broadcast progress (throttle to every 5 lines for performance)
                    if self.broadcast_callback and line_num % 5 == 0:
                        await self.broadcast_callback({
                            'type': 'file_status',
                            'filename': self.filename,
                            'current': line_num + 1,
                            'total': self.total_lines,
                            'percent': (line_num + 1) / self.total_lines * 100,
                            'current_gcode': gcode,
                        })
                else:
                    await asyncio.sleep(0.01)
        finally:
            self.grbl.streaming = False

        # Done
        self.running = False
        self._save_recovery()

        if self.current_line >= self.total_lines and not self.stop_flag:
            elog(f'STREAMER: Completed {self.filename}')

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
        elog('STREAMER: Paused')

    def resume(self):
        """Resume streaming."""
        self.paused = False
        elog('STREAMER: Resumed')

    def stop(self):
        """Stop streaming."""
        self.stop_flag = True
        self.running = False
        self.paused = False
        self.air_cut = False
        if self.macros:
            self.macros.air_cut = False
        self.continue_event.set()  # unblock any waiting Z clamp
        self._save_recovery()
        elog('STREAMER: Stopped')

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
        self.streamer.macros = self.macros   # Give streamer access to tool change macro

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
                except Exception as e:
                    elog(f'WS HANDLER ERROR: {e}')
                    import traceback
                    elog(traceback.format_exc())
        finally:
            self.clients.discard(websocket)
            print(f'[WS] Client disconnected ({len(self.clients)} total)')

    async def _soft_reset_and_restore_wco(self, log_prefix: str, saved_wco: dict = None):
        """Soft-reset GRBL and restore work coordinates from saved WCO."""
        if saved_wco is None:
            saved_wco = dict(self.grbl.wco_cached)
        self.grbl.send_realtime(b'\x18')
        await asyncio.sleep(1.5)
        await self.grbl.send_command('$X')
        await asyncio.sleep(0.3)
        mpos = self.grbl.status.mpos
        await self.grbl.send_command(
            f'G10 L20 P1 X{mpos["x"] - saved_wco["x"]:.3f} '
            f'Y{mpos["y"] - saved_wco["y"]:.3f} '
            f'Z{mpos["z"] - saved_wco["z"]:.3f} '
            f'A{mpos["a"] - saved_wco["a"]:.3f}'
        )
        elog(f'{log_prefix}: Coordinates restored (WCO X{saved_wco["x"]:.3f} Y{saved_wco["y"]:.3f} Z{saved_wco["z"]:.3f})')

    async def handle_message(self, ws, msg: Dict[str, Any]):
        """Route incoming WebSocket message."""
        msg_type = msg.get('type', '')
        # Log all user actions
        if msg_type not in ('settings',):  # skip noisy polling
            details = {k: v for k, v in msg.items() if k != 'type' and k != 'content'}
            elog(f'UI: {msg_type} {details if details else ""}')

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
            self.macros.cancel()
            await self._soft_reset_and_restore_wco('RESET')

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
            if from_line > 1:
                skip_check = True
            air_cut = msg.get('air_cut', False)
            success, error_msg = await self.streamer.start(from_line, skip_position_check=skip_check, air_cut=air_cut)
            if not success:
                elog(f'FILE START ERROR: {error_msg}')
                await ws.send(json.dumps({'type': 'file_start_error', 'error': error_msg}))

        elif msg_type == 'file_pause':
            self.streamer.pause()

        elif msg_type == 'file_resume':
            self.streamer.resume()

        elif msg_type == 'file_stop':
            # Save WCO immediately
            saved_wco = dict(self.grbl.wco_cached)
            self.streamer.stop()
            # Feed hold stops motion
            self.grbl.send_realtime(b'!')
            for _ in range(50):
                if self.grbl.status.state in ('Hold:0',):
                    break
                await asyncio.sleep(0.1)
            # Soft reset to flush GRBL buffer
            self.grbl.send_realtime(b'\x18')
            await asyncio.sleep(1.5)
            await self.grbl.send_command('$X')
            await asyncio.sleep(0.3)
            # Restore coordinates from saved WCO
            mpos = self.grbl.status.mpos
            await self.grbl.send_command(
                f'G10 L20 P1 X{mpos["x"] - saved_wco["x"]:.3f} '
                f'Y{mpos["y"] - saved_wco["y"]:.3f} '
                f'Z{mpos["z"] - saved_wco["z"]:.3f} '
                f'A{mpos["a"] - saved_wco["a"]:.3f}'
            )
            # Drain software queues
            for q in (self.grbl.stream_queue, self.grbl.response_queue):
                while not q.empty():
                    try:
                        q.get_nowait()
                    except:
                        break
            self.grbl.streaming = False
            # Spindle off and raise Z to top
            await self.grbl.send_command('M5')
            await self.grbl.send_command('G53 G0 Z-2')
            elog(f'STREAMER: Stop complete (WCO restored X{saved_wco["x"]:.3f} Y{saved_wco["y"]:.3f} Z{saved_wco["z"]:.3f})')

        elif msg_type == 'home':
            axes = msg.get('axes', 'ZXY')
            reset_a = msg.get('reset_a', axes == 'ZXY')
            elog(f'HOMING requested: axes={axes}')
            asyncio.create_task(self.macros.run_homing(axes, reset_a))

        elif msg_type == 'macro_run':
            name = msg.get('name', '')
            # Map button names to macro file names
            name_map = {
                'set_z': 'tool_measure',
                'tool_change': 'tool_change',
            }
            macro_name = name_map.get(name, name)
            # Pass all message params (except type and name) to macro
            params = {k: v for k, v in msg.items() if k not in ('type', 'name')}
            if 'tool_diameter' not in params:
                params['tool_diameter'] = 6.35  # Default to 1/4"
            elog(f'MACRO requested: {macro_name} params={params}')
            asyncio.create_task(self.macros.run_macro(macro_name, **params))

        elif msg_type == 'macro_continue':
            self.macros.continue_macro()
            self.streamer.continue_stream()

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

        elif msg_type == 'client_log':
            elog(f'HTML: {msg.get("message", "")}')

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

    # Log version of all code files at startup
    import hashlib, glob
    elog(f'SERVER VERSION: {VERSION}')
    for f in sorted(glob.glob('*.py') + glob.glob('macros/*.py')):
        h = hashlib.md5(open(f, 'rb').read()).hexdigest()[:8]
        elog(f'  {f}: {h}')
    elog(f'  jog.html: {hashlib.md5(open("jog.html", "rb").read()).hexdigest()[:8]}')

    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        print('\n[Server] Shutting down...')

if __name__ == '__main__':
    main()
