"""
CNC Macros - SetZ and ToolChange
"""
import asyncio
import time
from typing import Optional, TYPE_CHECKING

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
        self.broadcast_callback = None

        # Stored values from SetZ
        self.probe_work_z: Optional[float] = None
        self.set_z_done: bool = False

    async def run_set_z(self):
        """
        Run the SetZ macro - exact CNCjs commands.
        """
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

            # Go to G28 position (X Y Z only, not A) using queried coordinates
            g28 = self.grbl.g28_pos
            await self._send_and_log(f'G53 G0 X{g28["x"]:.3f} Y{g28["y"]:.3f} Z{g28["z"]:.3f}')
            await self._wait_idle()

            # G90
            await self._send_and_log('G90')

            # G38.2 Z-78 F300
            await self._send_and_log('G38.2 Z-78 F300')
            await self._wait_idle()

            # G91
            await self._send_and_log('G91')

            # G0 Z2
            await self._send_and_log('G0 Z2')
            await self._wait_idle()

            # G38.2 Z-4 F10
            await self._send_and_log('G38.2 Z-4 F10')
            await self._wait_idle()

            # G90
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

            # G0 X[startX] Y[startY]
            await self._send_and_log(f'G0 X{start_x:.3f} Y{start_y:.3f}')
            await self._wait_idle()

            # Return Z to exact start position
            await self._send_and_log(f'G0 Z{start_z:.3f}')
            await self._wait_idle()

            await self._log('=== SET_Z COMPLETE ===')
            await self._report_done()

        except Exception as e:
            await self._log(f'SET_Z ERROR: {e}')
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

    async def _log(self, msg: str):
        """Log message to clients (shows in debug console)."""
        if self.broadcast_callback:
            await self.broadcast_callback({
                'type': 'macro_log',
                'name': self.current_macro,
                'message': msg,
            })

    async def _send_and_log(self, gcode: str):
        """Send G-code command and log it."""
        await self._log(f'> {gcode}')
        await self.grbl.send_command(gcode)

    async def _wait_idle(self, timeout: float = 30.0):
        """Wait for machine to reach Idle state."""
        start = time.time()
        while time.time() - start < timeout:
            if self.cancel_flag:
                raise Exception('Macro cancelled')
            if self.grbl.status.state == 'Idle':
                return
            await asyncio.sleep(0.1)
        raise Exception(f'Timeout waiting for Idle (stuck in {self.grbl.status.state})')

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
        self.continue_event.set()
