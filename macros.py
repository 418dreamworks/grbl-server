"""
CNC Macros - SetZ and ToolChange
"""
import asyncio
import logging
import time
from pathlib import Path
from typing import Optional, TYPE_CHECKING

_elog = logging.getLogger('cnc_errors')

if TYPE_CHECKING:
    from grbl_server import GrblConnection

# Probe settings
PROBE_FEED_FAST = 150
PROBE_FEED_SLOW = 20
PROBE_DISTANCE = 20
PROBE_BACKOFF = 2
TOOL_CHANGE_X = -2
TOOL_CHANGE_Y = -418
SAFE_Z = -45

# Zero probe settings
Z_PLATE_THICKNESS = 22.0      # Probe plate is 22mm above work surface
PROBE_EDGE_OFFSET = 7.0       # Probe edge is 7mm from work edge (X and Y)
TOOL_DIA_QUARTER = 6.35       # 1/4" = 6.35mm
TOOL_DIA_EIGHTH = 3.175       # 1/8" = 3.175mm


class MacroEngine:
    """Handles SetZ and ToolChange macros."""

    def __init__(self, grbl: 'GrblConnection'):
        self.grbl = grbl
        self.running: bool = False
        self.current_macro: str = ''
        self.current_step: int = 0
        self.total_steps: int = 0
        self.waiting_continue: bool = False
        self.continue_event: asyncio.Event = asyncio.Event()
        self.cancel_flag: bool = False
        self.skip_flag: bool = False
        self.broadcast_callback = None
        self.notify_callback = None  # Called when user action required (e.g. tool change)

        # Stored values from SetZ
        self.probe_work_z: Optional[float] = None
        self.set_z_done: bool = False
        self.homing_ok: bool = False  # True after successful homing
        self.last_error: str = ''  # Set by _report_error, cleared by _report_done

        # Tool diameter for probing macros (can be set before running)
        self.tool_diameter: float = TOOL_DIA_QUARTER  # Default to 1/4"

        # Edge sign for X/Y probes: -1=left/front, +1=right/back
        self.edge_sign: int = -1  # Default to left/front edge

        # Streamer reference (set by CNCServer for access to loaded G-code)
        self.streamer = None

        # Air cut mode — when True, all spindle/coolant commands are skipped
        self.air_cut: bool = False

        # Fixtures list: [{x, y, z, radius}, ...]
        self.fixtures: list = []

    @property
    def loaded_gcode(self) -> str:
        """Get loaded G-code from streamer as a single string."""
        if self.streamer and hasattr(self.streamer, 'lines'):
            return '\n'.join(self.streamer.lines)
        return ''

    async def broadcast_fixtures(self):
        """Broadcast current fixtures list to clients."""
        if self.broadcast_callback:
            await self.broadcast_callback({
                'type': 'fixtures',
                'fixtures': self.fixtures
            })

    def check_collisions(self) -> list:
        """
        Check loaded G-code for collisions with fixtures.

        Returns list of collisions: [{line, x, y, z, fixture_index}, ...]
        """
        import re
        import math

        if not self.loaded_gcode or not self.fixtures:
            return []

        collisions = []
        current_x, current_y, current_z = 0.0, 0.0, 0.0
        absolute_mode = True

        for line_num, line in enumerate(self.loaded_gcode.splitlines(), 1):
            line = line.split(';')[0].strip()
            if not line:
                continue

            # Track G90/G91 mode
            if re.search(r'\bG90\b', line, re.IGNORECASE):
                absolute_mode = True
            if re.search(r'\bG91\b', line, re.IGNORECASE):
                absolute_mode = False

            # Extract coordinates
            x_match = re.search(r'X([-\d.]+)', line, re.IGNORECASE)
            y_match = re.search(r'Y([-\d.]+)', line, re.IGNORECASE)
            z_match = re.search(r'Z([-\d.]+)', line, re.IGNORECASE)

            if x_match:
                val = float(x_match.group(1))
                current_x = val if absolute_mode else current_x + val
            if y_match:
                val = float(y_match.group(1))
                current_y = val if absolute_mode else current_y + val
            if z_match:
                val = float(z_match.group(1))
                current_z = val if absolute_mode else current_z + val

            # Only check G1 moves (cutting moves)
            if not re.match(r'G0*1\b', line, re.IGNORECASE):
                continue

            # Check against each fixture
            for idx, fixture in enumerate(self.fixtures):
                # Fixture is a cylinder: center at (x, y), top at z, radius
                dist = math.sqrt((current_x - fixture['x'])**2 + (current_y - fixture['y'])**2)

                # Collision if within radius AND below fixture top
                if dist < fixture['radius'] and current_z <= fixture['z']:
                    collisions.append({
                        'line': line_num,
                        'x': round(current_x, 3),
                        'y': round(current_y, 3),
                        'z': round(current_z, 3),
                        'fixture_index': idx
                    })

        return collisions

    async def run_set_z(self):
        """
        Run the SetZ macro - exact CNCjs commands.
        """
        if not self.homing_ok:
            await self._report_error('Home the machine first')
            return
        self.current_macro = 'set_z'
        self.running = True
        self.cancel_flag = False

        try:
            # %wait before capturing start position
            await self._wait_idle()

            # %startX = posx, %startY = posy, %startZ = posz
            start_x = self.grbl.status.wpos['x']
            start_y = self.grbl.status.wpos['y']
            start_z = self.grbl.status.wpos['z']
            await self._log(f'start: X{start_x:.3f} Y{start_y:.3f} Z{start_z:.3f}')

            # G53 G0 Z-1
            await self._send_and_log('G53 G0 Z-1')
            await self._wait_idle()

            # %offset = posz - startZ
            offset = self.grbl.status.wpos['z'] - start_z
            await self._log(f'offset: {offset:.3f}')

            # G10 L20 P1 Z-1
            await self._send_and_log('G10 L20 P1 Z-1')

            # Refresh G28 position from controller
            await self.grbl.send_command('$#')
            g28 = self.grbl.g28_pos
            await self._log(f'G28 pos: X{g28["x"]:.3f} Y{g28["y"]:.3f} Z{g28["z"]:.3f}')

            # Abort if G28 not set
            if g28['x'] == 0 and g28['y'] == 0 and g28['z'] == 0:
                await self._log('ERROR: Probe position not set! Jog to probe plate and send G28.1')
                await self._report_error('Probe position not set. Jog to probe plate and send G28.1')
                return

            await self._send_and_log(f'G53 G0 X{g28["x"]:.3f} Y{g28["y"]:.3f} Z{g28["z"]:.3f}')
            await self._wait_idle()

            # G90
            await self._send_and_log('G90')

            # Probe fast — use Z max travel from settings
            z_max = float(self.grbl.settings.get('$132', 80))
            await self._send_and_log(f'G38.2 Z-{z_max - 2:.0f} F600')
            await self._wait_idle()

            # Back off — user can adjust XY before slow probe
            await self._send_and_log('G91')
            await self._send_and_log('G0 Z2')
            await self._wait_idle()
            await self._send_and_log('G90')
            await self._log('Adjust XY if needed, then press CONTINUE for slow probe')
            await self._wait_for_continue()
            await self._send_and_log('G91')
            await self._send_and_log('G38.2 Z-4 F1')
            await self._wait_idle()
            await self._send_and_log('G90')

            # %global.probeWorkZ = mposz
            self.probe_work_z = self.grbl.status.mpos['z']
            self.set_z_done = True
            await self._log(f'probeWorkZ = {self.probe_work_z:.3f} (machine)')

            # G53 G0 Z-1
            await self._send_and_log('G53 G0 Z-1')
            await self._wait_idle()

            # G10 L20 P1 Z[offset + startZ]
            restore_z = offset + start_z
            await self._send_and_log(f'G10 L20 P1 Z{restore_z:.3f}')

            # Z top, then XY return
            await self._send_and_log('G53 G0 Z-2')
            await self._wait_idle()
            await self._send_and_log(f'G0 X{start_x:.3f} Y{start_y:.3f}')
            await self._wait_idle()

            await self._log('=== SET_Z COMPLETE ===')
            await self._report_done()

        except Exception as e:
            await self._log(f'SET_Z ERROR: {e}')
            try:
                await self._send_and_log('G90')
                await self._send_and_log('G53 G0 Z-1')
                await self._wait_idle()
                await self._send_and_log(f'G0 X{start_x:.3f} Y{start_y:.3f}')
                await self._wait_idle()
                await self._log('Returned to start after error')
            except:
                pass
            await self._report_error(str(e))
        finally:
            self.running = False

    async def _probe_at_g28(self):
        """Go to G28, fast probe, slow probe, store probe_work_z, return to safe Z."""
        # Refresh G28 position
        await self.grbl.send_command('$#')
        g28 = self.grbl.g28_pos
        await self._log(f'G28 pos: X{g28["x"]:.3f} Y{g28["y"]:.3f} Z{g28["z"]:.3f}')

        if g28['x'] == 0 and g28['y'] == 0 and g28['z'] == 0:
            raise Exception('G28 position not set. Jog to probe plate and send G28.1')

        # Go to G28 probe position: safe Z, then XY, then Z
        await self._send_and_log('G53 G0 Z-1')
        await self._wait_idle()
        await self._send_and_log(f'G53 G0 X{g28["x"]:.3f} Y{g28["y"]:.3f}')
        await self._wait_idle()
        await self._send_and_log(f'G53 G0 Z{g28["z"]:.3f}')
        await self._wait_idle()

        # Probe fast — calculate available travel from current position
        await self.grbl.send_command('$$')
        if '$132' not in self.grbl.settings:
            raise Exception('Cannot read Z soft limit ($132) from controller')
        z_max = float(self.grbl.settings['$132'])
        current_z = self.grbl.status.mpos['z']
        probe_distance = z_max + current_z
        await self._log(f'Z soft limit: {z_max} MPos Z: {current_z:.3f} probe travel: {probe_distance:.1f}')
        await self._send_and_log('G91')
        await self._send_and_log(f'G38.2 Z-{probe_distance:.1f} F600')
        await self._wait_idle()

        # Back off — user can adjust XY before slow probe
        await self._log(f'Fast probe contact: MPos Z={self.grbl.status.mpos["z"]:.3f} feed_override={self.grbl.status.feed_override}%')
        await self._send_and_log('G0 Z2')
        await self._wait_idle()
        await self._log(f'After backoff: MPos Z={self.grbl.status.mpos["z"]:.3f}')
        await self._send_and_log('G90')
        await self._log('Adjust XY if needed, then press CONTINUE for slow probe')
        await self._wait_for_continue()
        await self._log(f'Before slow probe: MPos Z={self.grbl.status.mpos["z"]:.3f} feed_override={self.grbl.status.feed_override}%')
        await self._send_and_log('G91')
        await self._send_and_log('G38.2 Z-8 F2')
        await self._wait_idle()
        await self._send_and_log('G90')

        # Store probe Z (machine coords)
        self.probe_work_z = self.grbl.status.mpos['z']
        self.set_z_done = True
        await self._log(f'probeWorkZ = {self.probe_work_z:.3f} (machine)')

        # Return to safe Z
        await self._send_and_log('G53 G0 Z-1')
        await self._wait_idle()

    async def run_tool_change(self):
        """Run the tool_change macro file."""
        await self.run_macro('tool_change')

    async def run_probe_z(self):
        """Run the probe_z macro file."""
        await self.run_macro('probe_z')

    async def run_probe_x(self, tool_diameter: float = TOOL_DIA_QUARTER, edge_sign: int = 1):
        """Run the probe_x macro file."""
        await self.run_macro('probe_x', tool_diameter=tool_diameter, edge_sign=edge_sign)

    async def run_probe_y(self, tool_diameter: float = TOOL_DIA_QUARTER, edge_sign: int = 1):
        """Run the probe_y macro file."""
        await self.run_macro('probe_y', tool_diameter=tool_diameter, edge_sign=edge_sign)

    async def run_probe_xy(self, tool_diameter: float = TOOL_DIA_QUARTER):
        """Run X then Y probe as one macro."""
        self.current_macro = 'probe_xy'
        self.running = True
        self.cancel_flag = False
        try:
            await self._exec_macro('probe_x', tool_diameter=tool_diameter, edge_sign=1)
            await self._exec_macro('probe_y', tool_diameter=tool_diameter, edge_sign=1)
            await self._report_done()
        except Exception as e:
            await self._report_error(str(e))
        finally:
            self.running = False

    async def run_probe_xyz(self, tool_diameter: float = TOOL_DIA_QUARTER):
        """Run Z, X, Y probe as one macro."""
        self.current_macro = 'probe_xyz'
        self.running = True
        self.cancel_flag = False
        try:
            await self._exec_macro('probe_z', tool_diameter=tool_diameter)
            await self._exec_macro('probe_x', tool_diameter=tool_diameter, edge_sign=1)
            await self._exec_macro('probe_y', tool_diameter=tool_diameter, edge_sign=1)
            await self._report_done()
        except Exception as e:
            await self._report_error(str(e))
        finally:
            self.running = False

    async def _log(self, msg: str):
        """Log message to clients (shows in debug console)."""
        if self.broadcast_callback:
            await self.broadcast_callback({
                'type': 'macro_log',
                'name': self.current_macro,
                'message': msg,
            })

    async def _send_and_log(self, gcode: str):
        """Send G-code command and log it. Skips spindle/coolant in air cut mode."""
        if self.cancel_flag:
            raise Exception('Macro cancelled')
        if self.air_cut:
            upper = gcode.upper().strip()
            if any(c in upper for c in ('M3', 'M4', 'M5', 'M7', 'M8', 'M9')) or upper.startswith('S'):
                await self._log(f'> {gcode} (skipped - air cut)')
                return
        await self._log(f'> {gcode}')
        await self.grbl.send_command(gcode)

    async def _stream_lines(self, lines: list):
        """Stream G-code lines using character-counting protocol for smooth motion."""
        import collections
        RX_BUF_SIZE = 128
        buf_used = 0
        sent_lines = collections.deque()
        send_idx = 0

        # Drain stale responses and enable streaming mode
        while not self.grbl.stream_queue.empty():
            try:
                self.grbl.stream_queue.get_nowait()
            except:
                break
        self.grbl.streaming = True

        try:
            while send_idx < len(lines) or sent_lines:
                if self.cancel_flag:
                    break

                # --- SEND: fill GRBL buffer ---
                while send_idx < len(lines) and not self.cancel_flag:
                    line = lines[send_idx].strip()
                    if not line:
                        send_idx += 1
                        continue
                    cmd_len = len(line + '\n')
                    if buf_used + cmd_len > RX_BUF_SIZE:
                        break  # buffer full
                    nbytes = self.grbl.send_stream_line(line)
                    buf_used += nbytes
                    sent_lines.append((nbytes, line))
                    send_idx += 1

                # --- RECEIVE: process one response ---
                if sent_lines:
                    try:
                        result_type, result = await asyncio.wait_for(
                            self.grbl.stream_queue.get(), timeout=20.0
                        )
                    except asyncio.TimeoutError:
                        await self._log(f'STREAM: Timeout waiting for response. sent={len(sent_lines)} buf={buf_used}/128 send_idx={send_idx}/{len(lines)} state={self.grbl.status.state} streaming={self.grbl.streaming} queue_size={self.grbl.stream_queue.qsize()}')
                        if sent_lines:
                            await self._log(f'STREAM: Waiting for ok on: {sent_lines[0][1]}')
                        break
                    nbytes, gcode = sent_lines.popleft()
                    buf_used -= nbytes
                    if 'error' in str(result):
                        await self._log(f'STREAM ERROR: {result} (cmd: {gcode})')
                        break
        finally:
            # Drain remaining responses
            while sent_lines:
                try:
                    await asyncio.wait_for(self.grbl.stream_queue.get(), timeout=5.0)
                    sent_lines.popleft()
                except:
                    break
            self.grbl.streaming = False

    async def _get_distance_mode(self) -> str:
        """Query current distance mode (G90 absolute or G91 relative)."""
        result = await self.grbl.send_command('$G')
        # Response: [GC:G0 G54 G17 G21 G90 G94 M5 M9 T0 F0 S0]
        if 'G91' in result:
            return 'G91'
        return 'G90'

    async def _wait_idle(self, timeout: float = 30.0):
        """Wait for machine to reach Idle state after movement completes."""
        start = time.time()

        # First, wait for machine to start moving (leave Idle state)
        # This prevents returning immediately before the move even starts
        while time.time() - start < 2.0:  # Max 2 sec to start moving
            if self.cancel_flag:
                raise Exception('Macro cancelled')
            if self.grbl.status.state != 'Idle':
                break  # Machine started moving
            await asyncio.sleep(0.05)

        # Now wait for machine to finish (return to Idle)
        while time.time() - start < timeout:
            if self.cancel_flag:
                raise Exception('Macro cancelled')
            if self.grbl.status.state == 'Idle':
                return
            await asyncio.sleep(0.1)
        raise Exception(f'Timeout waiting for Idle (stuck in {self.grbl.status.state})')

    async def _wait_for_continue(self):
        """Wait for user to click Continue button."""
        if self.cancel_flag:
            raise Exception('Macro cancelled')
        # Clear event before waiting
        self.continue_event.clear()
        # Notify client that we're waiting
        if self.broadcast_callback:
            await self.broadcast_callback({
                'type': 'macro_status',
                'name': self.current_macro,
                'step': self.current_step,
                'waiting': True,
            })
        # Wait for continue or cancel
        await self.continue_event.wait()
        if self.cancel_flag:
            raise Exception('Macro cancelled')

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
        """Report macro completion. Clears last_error."""
        self.last_error = ''
        if self.broadcast_callback:
            await self.broadcast_callback({
                'type': 'macro_done',
                'name': self.current_macro,
            })

    async def _report_error(self, error: str):
        """Report macro error. Sets last_error for callers to check."""
        self.last_error = error
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
        self.continue_event.set()

    async def run_debug_macro(self):
        """Run the debug_macro.py file."""
        self.current_macro = 'debug'
        self.running = True
        self.cancel_flag = False

        debug_path = Path(__file__).parent / 'debug_macro.py'

        try:
            if not debug_path.exists():
                await self._log('debug_macro.py not found')
                await self._report_error('debug_macro.py not found')
                return

            await self._log('=== DEBUG MACRO START ===')

            # Read and exec the debug macro
            code = debug_path.read_text()

            # Create a namespace with access to self (the MacroEngine)
            namespace = {
                'self': self,
                'asyncio': asyncio,
            }

            # Wrap code in an async function and execute it
            wrapped = f"async def _debug_macro():\n"
            for line in code.split('\n'):
                wrapped += f"    {line}\n"
            wrapped += "\nasyncio.get_event_loop().run_until_complete(_debug_macro())"

            # Actually, better approach - use exec with async
            exec(f"import asyncio\nasync def _run():\n" +
                 '\n'.join('    ' + line for line in code.split('\n')),
                 namespace)

            await namespace['_run']()

            await self._log('=== DEBUG MACRO COMPLETE ===')
            await self._report_done()

        except Exception as e:
            await self._log(f'DEBUG MACRO ERROR: {e}')
            await self._report_error(str(e))
        finally:
            self.running = False

    async def run_homing(self, axes: str = 'ZXY', reset_a: bool = True):
        """
        Home specified axes while preserving work coordinates.

        Saves WCO before homing, homes each axis sequentially,
        then restores WCO so work coordinates remain correct.

        WPos = MPos - WCO, so after homing (MPos changes),
        we set WPos = new_MPos - saved_WCO via G10 L20.

        Args:
            axes: String of axes to home, e.g. 'Z', 'X', 'ZXY'
            reset_a: If True, reset A axis offset (for rotary accumulation)
        """
        self.current_macro = 'homing'
        self.running = True
        self.cancel_flag = False

        try:
            await self._log(f'=== HOMING {axes} ===')

            # Save current WCO (Work Coordinate Offset)
            await self._wait_idle()
            saved_wco = {
                'x': self.grbl.wco_cached['x'],
                'y': self.grbl.wco_cached['y'],
                'z': self.grbl.wco_cached['z'],
            }
            await self._log(f'Saved WCO: X{saved_wco["x"]:.3f} Y{saved_wco["y"]:.3f} Z{saved_wco["z"]:.3f}')

            # Reset A axis if requested (instead of $RST=# which wipes all offsets)
            if reset_a:
                result = await self.grbl.send_command('G10 L20 P1 A0')
                if result.startswith('error'):
                    await self._log(f'A axis reset skipped ({result})')
                else:
                    await self._log('A axis offset reset')

            # Home each axis sequentially
            for axis in axes.upper():
                if axis not in 'XYZ':
                    continue
                await self._log(f'Homing {axis}...')
                await self.grbl.send_command(f'$H{axis}')
                # Wait for homing to complete (state goes Home -> Idle)
                await self._wait_idle(timeout=30.0)

            # Restore WCO: set WPos = new_MPos - saved_WCO
            await self._wait_idle()
            restore_parts = []
            for axis in axes.upper():
                if axis in 'XYZ':
                    key = axis.lower()
                    new_wpos = self.grbl.status.mpos[key] - saved_wco[key]
                    restore_parts.append(f'{axis}{new_wpos:.3f}')

            if restore_parts:
                restore_cmd = 'G10 L20 P1 ' + ' '.join(restore_parts)
                await self._send_and_log(restore_cmd)
                await self._log(f'WCO restored')

            self.homing_ok = True
            await self._log('=== HOMING COMPLETE ===')
            await self._report_done()

        except Exception as e:
            import traceback
            self.homing_ok = False
            _elog.error(f'HOMING ERROR: {e}\n{traceback.format_exc()}')
            await self._log(f'HOMING ERROR: {e}')
            await self._report_error(str(e))
        finally:
            self.running = False

    async def _exec_macro(self, name: str, **kwargs):
        """Execute a macro file without state management (no running/done/error).
        Use this to compose macros from other macros."""
        for key, value in kwargs.items():
            setattr(self, key, value)

        macro_path = Path(__file__).parent / 'macros' / f'{name}.py'
        if not macro_path.exists():
            raise Exception(f'Macro file not found: {macro_path}')

        code = macro_path.read_text()
        import math
        macro_dir = str(macro_path.parent)
        namespace = {
            'self': self,
            'asyncio': asyncio,
            'math': math,
            'macro_dir': macro_dir,
        }
        exec(f"import asyncio\nimport math\nasync def _run():\n" +
             '\n'.join('    ' + line for line in code.split('\n')),
             namespace)
        await namespace['_run']()

    async def run_macro(self, name: str, **kwargs):
        """
        Run a macro from macros/{name}.py file.

        The macro file contains raw async code that will be wrapped in an
        async function and executed with self (MacroEngine) in the namespace.

        Args:
            name: Macro name (without .py extension)
            **kwargs: Additional parameters to set on self before running
        """
        self.current_macro = name
        self.running = True
        self.cancel_flag = False

        # Apply any kwargs as attributes (e.g., tool_diameter)
        for key, value in kwargs.items():
            setattr(self, key, value)

        # Notify UI that macro is starting
        if self.broadcast_callback:
            await self.broadcast_callback({
                'type': 'macro_status',
                'name': name,
                'step': 0,
                'total': 1,
                'description': f'Running {name}',
                'command': '',
                'waiting': False,
            })

        macro_path = Path(__file__).parent / 'macros' / f'{name}.py'

        try:
            if not macro_path.exists():
                await self._log(f'Macro not found: {name}')
                await self._report_error(f'Macro file not found: {macro_path}')
                return

            # Read macro code
            code = macro_path.read_text()

            # Create namespace with access to self and common imports
            import math
            macro_dir = str(macro_path.parent)
            namespace = {
                'self': self,
                'asyncio': asyncio,
                'math': math,
                'macro_dir': macro_dir,
            }

            # Wrap code in async function and execute
            exec(f"import asyncio\nimport math\nasync def _run():\n" +
                 '\n'.join('    ' + line for line in code.split('\n')),
                 namespace)

            await namespace['_run']()
            await self._report_done()

        except Exception as e:
            import traceback
            _elog.error(f'MACRO ERROR ({name}): {e}\n{traceback.format_exc()}')
            await self._log(f'MACRO ERROR ({name}): {e}')
            await self._report_error(str(e))
        finally:
            self.running = False
