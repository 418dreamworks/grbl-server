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
from websockets.server import WebSocketServerProtocol
from http import HTTPStatus

# ============================================================
# CONFIGURATION
# ============================================================

DEFAULT_HTTP_PORT = 8000
DEFAULT_SERIAL_PORT = '/dev/ttyACM0'
DEFAULT_BAUD_RATE = 115200
STATUS_POLL_INTERVAL = 0.2  # 200ms
RECOVERY_SAVE_INTERVAL = 100  # Save recovery every N lines

# Probe settings (from ~/.cncrc macros)
PROBE_FEED_FAST = 150
PROBE_FEED_SLOW = 20
PROBE_DISTANCE = 20
PROBE_BACKOFF = 2
TOOL_CHANGE_X = -2
TOOL_CHANGE_Y = -418
SAFE_Z = -45

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

    def __init__(self):
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

            # Request settings
            await self.send_command('$$')

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

        # Settings: $N=value
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
# MACRO ENGINE
# ============================================================

class MacroEngine:
    """Handles SetZ and ToolChange macros."""

    def __init__(self, grbl: GrblConnection):
        self.grbl = grbl
        self.running: bool = False
        self.current_macro: str = ''
        self.current_step: int = 0
        self.total_steps: int = 0
        self.waiting_continue: bool = False
        self.continue_event: asyncio.Event = asyncio.Event()
        self.cancel_flag: bool = False
        self.broadcast_callback = None

        # Stored values from SetZ
        self.probe_work_z: Optional[float] = None
        self.set_z_done: bool = False

    async def run_set_z(self):
        """Run the SetZ macro."""
        self.current_macro = 'set_z'
        self.running = True
        self.cancel_flag = False

        steps = [
            ('Save position', 'Saving current XY position'),
            ('Raise Z', f'G53 G0 Z{SAFE_Z}'),
            ('Probe fast', f'G38.2 Z-{PROBE_DISTANCE} F{PROBE_FEED_FAST}'),
            ('Back off', f'G91 G0 Z{PROBE_BACKOFF}'),
            ('Probe slow', f'G38.2 Z-{PROBE_BACKOFF + 2} F{PROBE_FEED_SLOW}'),
            ('Store probe Z', 'Recording work Z at probe'),
            ('Restore position', 'Returning to saved XY'),
        ]

        self.total_steps = len(steps)
        saved_x = self.grbl.status.mpos['x']
        saved_y = self.grbl.status.mpos['y']

        try:
            for i, (name, cmd) in enumerate(steps):
                if self.cancel_flag:
                    break

                self.current_step = i + 1
                await self._report_step(name, cmd)

                if i == 0:  # Save position
                    saved_x = self.grbl.status.mpos['x']
                    saved_y = self.grbl.status.mpos['y']

                elif i == 1:  # Raise Z
                    await self.grbl.send_command(f'G53 G0 Z{SAFE_Z}')
                    await asyncio.sleep(2)  # Wait for move

                elif i == 2:  # Probe fast
                    await self.grbl.send_command(f'G38.2 Z-{PROBE_DISTANCE} F{PROBE_FEED_FAST}')
                    await asyncio.sleep(3)

                elif i == 3:  # Back off
                    await self.grbl.send_command('G91')
                    await self.grbl.send_command(f'G0 Z{PROBE_BACKOFF}')
                    await self.grbl.send_command('G90')
                    await asyncio.sleep(1)

                elif i == 4:  # Probe slow
                    await self.grbl.send_command('G91')
                    await self.grbl.send_command(f'G38.2 Z-{PROBE_BACKOFF + 2} F{PROBE_FEED_SLOW}')
                    await self.grbl.send_command('G90')
                    await asyncio.sleep(2)

                elif i == 5:  # Store probe Z
                    self.probe_work_z = self.grbl.status.wpos['z']
                    self.set_z_done = True

                elif i == 6:  # Restore
                    await self.grbl.send_command(f'G53 G0 Z{SAFE_Z}')
                    await asyncio.sleep(2)
                    await self.grbl.send_command(f'G53 G0 X{saved_x:.3f} Y{saved_y:.3f}')
                    await asyncio.sleep(2)

            if not self.cancel_flag:
                await self._report_done()

        except Exception as e:
            await self._report_error(str(e))
        finally:
            self.running = False

    async def run_tool_change(self):
        """Run the ToolChange macro."""
        if not self.set_z_done or self.probe_work_z is None:
            await self._report_error('SetZ must be run first')
            return

        self.current_macro = 'tool_change'
        self.running = True
        self.cancel_flag = False

        steps = [
            ('Save position', 'Saving current XY position'),
            ('Raise Z', f'G53 G0 Z{SAFE_Z}'),
            ('Move to probe', f'G53 G0 X{TOOL_CHANGE_X} Y{TOOL_CHANGE_Y}'),
            ('Wait for tool change', 'CONTINUE when tool is changed'),
            ('Probe fast', f'G38.2 Z-{PROBE_DISTANCE} F{PROBE_FEED_FAST}'),
            ('Back off', f'G91 G0 Z{PROBE_BACKOFF}'),
            ('Probe slow', f'G38.2 Z-{PROBE_BACKOFF + 2} F{PROBE_FEED_SLOW}'),
            ('Calculate offset', 'Computing Z offset from first probe'),
            ('Restore position', 'Returning to saved XY with offset'),
        ]

        self.total_steps = len(steps)
        saved_x = self.grbl.status.mpos['x']
        saved_y = self.grbl.status.mpos['y']
        new_probe_z = None

        try:
            for i, (name, cmd) in enumerate(steps):
                if self.cancel_flag:
                    break

                self.current_step = i + 1
                await self._report_step(name, cmd)

                if i == 0:  # Save position
                    saved_x = self.grbl.status.mpos['x']
                    saved_y = self.grbl.status.mpos['y']

                elif i == 1:  # Raise Z
                    await self.grbl.send_command(f'G53 G0 Z{SAFE_Z}')
                    await asyncio.sleep(2)

                elif i == 2:  # Move to probe
                    await self.grbl.send_command(f'G53 G0 X{TOOL_CHANGE_X} Y{TOOL_CHANGE_Y}')
                    await asyncio.sleep(5)

                elif i == 3:  # Wait for tool change
                    self.waiting_continue = True
                    self.continue_event.clear()
                    await self._report_step(name, cmd, waiting=True)
                    await self.continue_event.wait()
                    self.waiting_continue = False

                elif i == 4:  # Probe fast
                    await self.grbl.send_command(f'G38.2 Z-{PROBE_DISTANCE} F{PROBE_FEED_FAST}')
                    await asyncio.sleep(3)

                elif i == 5:  # Back off
                    await self.grbl.send_command('G91')
                    await self.grbl.send_command(f'G0 Z{PROBE_BACKOFF}')
                    await self.grbl.send_command('G90')
                    await asyncio.sleep(1)

                elif i == 6:  # Probe slow
                    await self.grbl.send_command('G91')
                    await self.grbl.send_command(f'G38.2 Z-{PROBE_BACKOFF + 2} F{PROBE_FEED_SLOW}')
                    await self.grbl.send_command('G90')
                    await asyncio.sleep(2)
                    new_probe_z = self.grbl.status.wpos['z']

                elif i == 7:  # Calculate offset
                    if new_probe_z is not None and self.probe_work_z is not None:
                        offset = new_probe_z - self.probe_work_z
                        # Apply offset to work Z
                        await self.grbl.send_command(f'G10 L20 P1 Z{-offset:.3f}')

                elif i == 8:  # Restore
                    await self.grbl.send_command(f'G53 G0 Z{SAFE_Z}')
                    await asyncio.sleep(2)
                    await self.grbl.send_command(f'G53 G0 X{saved_x:.3f} Y{saved_y:.3f}')
                    await asyncio.sleep(2)

            if not self.cancel_flag:
                await self._report_done()

        except Exception as e:
            await self._report_error(str(e))
        finally:
            self.running = False

    async def _report_step(self, name: str, cmd: str, waiting: bool = False):
        """Report macro step to clients."""
        if self.broadcast_callback:
            await self.broadcast_callback({
                'type': 'macro_status',
                'name': self.current_macro,
                'step': self.current_step,
                'total': self.total_steps,
                'description': name,
                'command': cmd,
                'waiting': waiting,
            })

    async def _report_done(self):
        """Report macro completion."""
        if self.broadcast_callback:
            await self.broadcast_callback({
                'type': 'macro_done',
                'name': self.current_macro,
            })

    async def _report_error(self, error: str):
        """Report macro error."""
        if self.broadcast_callback:
            await self.broadcast_callback({
                'type': 'macro_error',
                'name': self.current_macro,
                'step': self.current_step,
                'error': error,
            })

    def continue_macro(self):
        """Continue from M0 wait."""
        self.continue_event.set()

    def cancel(self):
        """Cancel running macro."""
        self.cancel_flag = True
        self.continue_event.set()  # Unblock any waiting

# ============================================================
# WEBSOCKET SERVER
# ============================================================

class GrblServer:
    """WebSocket server for CNC control."""

    def __init__(self, http_port: int, serial_port: str):
        self.http_port = http_port
        self.serial_port = serial_port
        self.grbl = GrblConnection()
        self.streamer = FileStreamer(self.grbl)
        self.macros = MacroEngine(self.grbl)
        self.clients: Set[WebSocketServerProtocol] = set()
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

    async def handle_client(self, websocket: WebSocketServerProtocol):
        """Handle WebSocket client connection."""
        self.clients.add(websocket)
        print(f'[WS] Client connected ({len(self.clients)} total)')

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

    async def handle_message(self, ws: WebSocketServerProtocol, msg: Dict[str, Any]):
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

    async def http_handler(self, path: str, request_headers):
        """Handle HTTP requests (serve jog.html)."""
        if path == '/' or path == '/index.html':
            return HTTPStatus.OK, [('Content-Type', 'text/html')], self.html_content.encode()
        return HTTPStatus.NOT_FOUND, [], b'Not Found'

    def load_html(self):
        """Load jog.html from same directory as script."""
        script_dir = Path(__file__).parent
        html_path = script_dir / 'jog.html'
        if html_path.exists():
            self.html_content = html_path.read_text()
            print(f'[Server] Loaded {html_path}')
        else:
            self.html_content = '<html><body><h1>jog.html not found</h1></body></html>'
            print(f'[Server] WARNING: {html_path} not found')

    async def start(self):
        """Start the server."""
        self.load_html()

        # Auto-connect to serial port
        if os.path.exists(self.serial_port):
            await self.grbl.connect(self.serial_port)

        # Start WebSocket server with HTTP handler
        async with websockets.serve(
            self.handle_client,
            '0.0.0.0',
            self.http_port,
            process_request=self.http_handler,
        ):
            print(f'[Server] Running on http://0.0.0.0:{self.http_port}')
            print(f'[Server] WebSocket at ws://0.0.0.0:{self.http_port}/ws')
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
