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
            await self._log(f'G28 pos: X{g28["x"]:.3f} Y{g28["y"]:.3f} Z{g28["z"]:.3f}')

            # Check if G28 position seems valid (not all zeros)
            if g28['x'] == 0 and g28['y'] == 0 and g28['z'] == 0:
                await self._log('WARNING: G28 position is 0,0,0 - may not be set! Use G28.1 to store probe position')

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
        """
        Run the ToolChange macro - exact CNCjs logic.

        CNCjs macro:
        %startX = posx, %startY = posy, %startZ = posz   ; Save WORK coords
        G53 G0 Z-1
        %offsetToSafe = posz - startZ
        G53 G0 X-2 Y-418
        M0                                               ; Wait for tool change
        G10 L20 P1 Z0                                    ; Zero work Z
        G28 X0 Y0 Z0                                     ; Go to G28 probe location
        G90
        G38.2 Z-78 F300                                  ; Probe fast
        G91
        G0 Z2                                            ; Back off
        G38.2 Z-4 F10                                    ; Probe slow
        G90
        %toolOffset = global.probeWorkZ - mposz          ; Calc offset using MACHINE Z
        G53 G0 Z-1
        G10 L20 P1 Z[startZ + offsetToSafe + toolOffset] ; Apply offset
        G0 X[startX] Y[startY]                           ; Return to start XY
        G0 Z[startZ]                                     ; Return to start Z
        """
        if not self.set_z_done or self.probe_work_z is None:
            await self._report_error('SetZ must be run first')
            return

        self.current_macro = 'tool_change'
        self.running = True
        self.cancel_flag = False

        try:
            # %wait before capturing start position
            await self._wait_idle()

            # %startX = posx, %startY = posy, %startZ = posz (WORK coords)
            start_x = self.grbl.status.wpos['x']
            start_y = self.grbl.status.wpos['y']
            start_z = self.grbl.status.wpos['z']
            await self._log(f'start: X{start_x:.3f} Y{start_y:.3f} Z{start_z:.3f}')

            # G53 G0 Z-1
            await self._send_and_log('G53 G0 Z-1')
            await self._wait_idle()

            # %offsetToSafe = posz - startZ
            offset_to_safe = self.grbl.status.wpos['z'] - start_z
            await self._log(f'offsetToSafe: {offset_to_safe:.3f}')

            # G53 G0 X-2 Y-418
            await self._send_and_log(f'G53 G0 X{TOOL_CHANGE_X} Y{TOOL_CHANGE_Y}')
            await self._wait_idle()

            # M0 - Wait for tool change
            self.waiting_continue = True
            self.continue_event.clear()
            if self.broadcast_callback:
                await self.broadcast_callback({
                    'type': 'macro_status',
                    'name': self.current_macro,
                    'step': 1,
                    'total': 1,
                    'description': 'Change tool and press CONTINUE',
                    'command': 'M0',
                    'waiting': True,
                })
            await self._log('=== WAITING FOR TOOL CHANGE ===')
            await self.continue_event.wait()
            self.waiting_continue = False
            await self._log('=== CONTINUING ===')

            # G10 L20 P1 Z0
            await self._send_and_log('G10 L20 P1 Z0')

            # Go to G28 position (X Y Z only, not A) using queried coordinates
            g28 = self.grbl.g28_pos
            await self._log(f'G28 pos: X{g28["x"]:.3f} Y{g28["y"]:.3f} Z{g28["z"]:.3f}')

            # Check if G28 position seems valid (not all zeros)
            if g28['x'] == 0 and g28['y'] == 0 and g28['z'] == 0:
                await self._log('WARNING: G28 position is 0,0,0 - may not be set! Use G28.1 to store probe position')

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

            # %toolOffset = global.probeWorkZ - mposz (MACHINE Z)
            new_mposz = self.grbl.status.mpos['z']
            tool_offset = self.probe_work_z - new_mposz
            await self._log(f'toolOffset: {tool_offset:.3f} (probeWorkZ={self.probe_work_z:.3f} - mposz={new_mposz:.3f})')

            # Update probeWorkZ for next tool change (same as SetZ)
            self.probe_work_z = new_mposz
            await self._log(f'probeWorkZ updated to {self.probe_work_z:.3f}')

            # G53 G0 Z-1
            await self._send_and_log('G53 G0 Z-1')
            await self._wait_idle()

            # G10 L20 P1 Z[startZ + offsetToSafe + toolOffset]
            restore_z = start_z + offset_to_safe + tool_offset
            await self._send_and_log(f'G10 L20 P1 Z{restore_z:.3f}')

            # G0 X[startX] Y[startY]
            await self._send_and_log(f'G0 X{start_x:.3f} Y{start_y:.3f}')
            await self._wait_idle()

            # G0 Z[startZ]
            await self._send_and_log(f'G0 Z{start_z:.3f}')
            await self._wait_idle()

            await self._log('=== TOOL_CHANGE COMPLETE ===')
            await self._report_done()

        except Exception as e:
            await self._log(f'TOOL_CHANGE ERROR: {e}')
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
