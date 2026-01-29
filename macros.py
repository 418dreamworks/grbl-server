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

    async def run_probe_z(self):
        """
        Z Zero Probe - probe down to find workpiece top surface.

        Plate is 22mm above work surface.
        Tool starts 0-10mm above probe surface.
        Sequence:
          1. Probe down at F50 until contact
          2. Back up 2mm, probe at F10
          3. Back up 0.5mm, probe at F5
          4. Set Z = 22 (plate thickness)
        """
        self.current_macro = 'probe_z'
        self.running = True
        self.cancel_flag = False

        try:
            await self._wait_idle()
            await self._log('=== Z PROBE START ===')

            # Switch to relative mode
            await self._send_and_log('G91')

            # First probe: fast (F50), max 15mm down
            await self._send_and_log('G38.2 Z-15 F50')
            await self._wait_idle()

            # Back off 2mm
            await self._send_and_log('G0 Z2')
            await self._wait_idle()

            # Second probe: medium (F10), max 10mm down
            await self._send_and_log('G38.2 Z-10 F10')
            await self._wait_idle()

            # Back off 0.5mm
            await self._send_and_log('G0 Z0.5')
            await self._wait_idle()

            # Third probe: slow (F5), max 5mm down
            await self._send_and_log('G38.2 Z-5 F5')
            await self._wait_idle()

            # Back to absolute mode
            await self._send_and_log('G90')

            # Set Z to plate thickness (work surface is at Z=0)
            await self._send_and_log(f'G10 L20 P1 Z{Z_PLATE_THICKNESS:.3f}')

            await self._log(f'Z set to {Z_PLATE_THICKNESS}mm (plate thickness)')
            await self._log('=== Z PROBE COMPLETE ===')
            await self._report_done()

        except Exception as e:
            await self._log(f'Z PROBE ERROR: {e}')
            await self._send_and_log('G90')  # Ensure back to absolute
            await self._report_error(str(e))
        finally:
            self.running = False

    async def run_probe_x(self, tool_diameter: float = TOOL_DIA_QUARTER):
        """
        X Zero Probe - probe right to find workpiece X edge.

        Tool starts to the LEFT of the probe/stock.
        Probe edge is 7mm to the LEFT of work X=0.

        Sequence:
          1. Move LEFT by (5 + tool_dia + 2)mm for safety
          2. Probe RIGHT at F50 until contact
          3. Back 2mm, probe at F10
          4. Back 0.5mm, probe at F5
          5. Set X = -(7 + tool_radius)
        """
        self.current_macro = 'probe_x'
        self.running = True
        self.cancel_flag = False

        tool_radius = tool_diameter / 2
        safety_move = 5 + tool_diameter + 2  # Move away distance

        try:
            await self._wait_idle()
            await self._log(f'=== X PROBE START (tool dia={tool_diameter:.3f}mm) ===')

            # Switch to relative mode
            await self._send_and_log('G91')

            # Move LEFT (away from stock) for safety
            await self._send_and_log(f'G0 X-{safety_move:.3f}')
            await self._wait_idle()

            # First probe: fast (F50), probe RIGHT
            await self._send_and_log('G38.2 X20 F50')
            await self._wait_idle()

            # Back off 2mm LEFT
            await self._send_and_log('G0 X-2')
            await self._wait_idle()

            # Second probe: medium (F10)
            await self._send_and_log('G38.2 X5 F10')
            await self._wait_idle()

            # Back off 0.5mm LEFT
            await self._send_and_log('G0 X-0.5')
            await self._wait_idle()

            # Third probe: slow (F5)
            await self._send_and_log('G38.2 X3 F5')
            await self._wait_idle()

            # Back to absolute mode
            await self._send_and_log('G90')

            # Set X: probe edge is 7mm left of work edge, plus tool radius
            x_offset = -(PROBE_EDGE_OFFSET + tool_radius)
            await self._send_and_log(f'G10 L20 P1 X{x_offset:.3f}')

            await self._log(f'X set to {x_offset:.3f}mm (edge offset + tool radius)')
            await self._log('=== X PROBE COMPLETE ===')
            await self._report_done()

        except Exception as e:
            await self._log(f'X PROBE ERROR: {e}')
            await self._send_and_log('G90')  # Ensure back to absolute
            await self._report_error(str(e))
        finally:
            self.running = False

    async def run_probe_y(self, tool_diameter: float = TOOL_DIA_QUARTER):
        """
        Y Zero Probe - probe forward to find workpiece Y edge.

        Tool starts BELOW (in front of) the probe/stock.
        Probe edge is 7mm BELOW work Y=0.

        Sequence:
          1. Move BACKWARD (-Y) by (5 + tool_dia + 2)mm for safety
          2. Probe FORWARD (+Y) at F50 until contact
          3. Back 2mm, probe at F10
          4. Back 0.5mm, probe at F5
          5. Set Y = -(7 + tool_radius)
        """
        self.current_macro = 'probe_y'
        self.running = True
        self.cancel_flag = False

        tool_radius = tool_diameter / 2
        safety_move = 5 + tool_diameter + 2  # Move away distance

        try:
            await self._wait_idle()
            await self._log(f'=== Y PROBE START (tool dia={tool_diameter:.3f}mm) ===')

            # Switch to relative mode
            await self._send_and_log('G91')

            # Move BACKWARD (away from stock) for safety
            await self._send_and_log(f'G0 Y-{safety_move:.3f}')
            await self._wait_idle()

            # First probe: fast (F50), probe FORWARD
            await self._send_and_log('G38.2 Y20 F50')
            await self._wait_idle()

            # Back off 2mm BACKWARD
            await self._send_and_log('G0 Y-2')
            await self._wait_idle()

            # Second probe: medium (F10)
            await self._send_and_log('G38.2 Y5 F10')
            await self._wait_idle()

            # Back off 0.5mm BACKWARD
            await self._send_and_log('G0 Y-0.5')
            await self._wait_idle()

            # Third probe: slow (F5)
            await self._send_and_log('G38.2 Y3 F5')
            await self._wait_idle()

            # Back to absolute mode
            await self._send_and_log('G90')

            # Set Y: probe edge is 7mm below work edge, plus tool radius
            y_offset = -(PROBE_EDGE_OFFSET + tool_radius)
            await self._send_and_log(f'G10 L20 P1 Y{y_offset:.3f}')

            await self._log(f'Y set to {y_offset:.3f}mm (edge offset + tool radius)')
            await self._log('=== Y PROBE COMPLETE ===')
            await self._report_done()

        except Exception as e:
            await self._log(f'Y PROBE ERROR: {e}')
            await self._send_and_log('G90')  # Ensure back to absolute
            await self._report_error(str(e))
        finally:
            self.running = False

    async def run_probe_xy(self, tool_diameter: float = TOOL_DIA_QUARTER):
        """XY Corner Probe - probe X then Y."""
        self.current_macro = 'probe_xy'
        self.running = True
        self.cancel_flag = False

        try:
            await self._log('=== XY PROBE START ===')

            # Run X probe (reuse logic but don't reset running state)
            await self._run_probe_x_internal(tool_diameter)

            # Run Y probe
            await self._run_probe_y_internal(tool_diameter)

            await self._log('=== XY PROBE COMPLETE ===')
            await self._report_done()

        except Exception as e:
            await self._log(f'XY PROBE ERROR: {e}')
            await self._send_and_log('G90')
            await self._report_error(str(e))
        finally:
            self.running = False

    async def run_probe_xyz(self, tool_diameter: float = TOOL_DIA_QUARTER):
        """XYZ Probe - probe Z, then X, then Y."""
        self.current_macro = 'probe_xyz'
        self.running = True
        self.cancel_flag = False

        try:
            await self._log('=== XYZ PROBE START ===')

            # Run Z probe first
            await self._run_probe_z_internal()

            # Run X probe
            await self._run_probe_x_internal(tool_diameter)

            # Run Y probe
            await self._run_probe_y_internal(tool_diameter)

            await self._log('=== XYZ PROBE COMPLETE ===')
            await self._report_done()

        except Exception as e:
            await self._log(f'XYZ PROBE ERROR: {e}')
            await self._send_and_log('G90')
            await self._report_error(str(e))
        finally:
            self.running = False

    async def _run_probe_z_internal(self):
        """Internal Z probe logic (no state management)."""
        await self._wait_idle()
        await self._log('--- Z probe ---')
        await self._send_and_log('G91')
        await self._send_and_log('G38.2 Z-15 F50')
        await self._wait_idle()
        await self._send_and_log('G0 Z2')
        await self._wait_idle()
        await self._send_and_log('G38.2 Z-10 F10')
        await self._wait_idle()
        await self._send_and_log('G0 Z0.5')
        await self._wait_idle()
        await self._send_and_log('G38.2 Z-5 F5')
        await self._wait_idle()
        await self._send_and_log('G90')
        await self._send_and_log(f'G10 L20 P1 Z{Z_PLATE_THICKNESS:.3f}')
        await self._log(f'Z set to {Z_PLATE_THICKNESS}mm')

    async def _run_probe_x_internal(self, tool_diameter: float):
        """Internal X probe logic (no state management)."""
        tool_radius = tool_diameter / 2
        safety_move = 5 + tool_diameter + 2
        await self._wait_idle()
        await self._log(f'--- X probe (tool={tool_diameter:.3f}mm) ---')
        await self._send_and_log('G91')
        await self._send_and_log(f'G0 X-{safety_move:.3f}')
        await self._wait_idle()
        await self._send_and_log('G38.2 X20 F50')
        await self._wait_idle()
        await self._send_and_log('G0 X-2')
        await self._wait_idle()
        await self._send_and_log('G38.2 X5 F10')
        await self._wait_idle()
        await self._send_and_log('G0 X-0.5')
        await self._wait_idle()
        await self._send_and_log('G38.2 X3 F5')
        await self._wait_idle()
        await self._send_and_log('G90')
        x_offset = -(PROBE_EDGE_OFFSET + tool_radius)
        await self._send_and_log(f'G10 L20 P1 X{x_offset:.3f}')
        await self._log(f'X set to {x_offset:.3f}mm')

    async def _run_probe_y_internal(self, tool_diameter: float):
        """Internal Y probe logic (no state management)."""
        tool_radius = tool_diameter / 2
        safety_move = 5 + tool_diameter + 2
        await self._wait_idle()
        await self._log(f'--- Y probe (tool={tool_diameter:.3f}mm) ---')
        await self._send_and_log('G91')
        await self._send_and_log(f'G0 Y-{safety_move:.3f}')
        await self._wait_idle()
        await self._send_and_log('G38.2 Y20 F50')
        await self._wait_idle()
        await self._send_and_log('G0 Y-2')
        await self._wait_idle()
        await self._send_and_log('G38.2 Y5 F10')
        await self._wait_idle()
        await self._send_and_log('G0 Y-0.5')
        await self._wait_idle()
        await self._send_and_log('G38.2 Y3 F5')
        await self._wait_idle()
        await self._send_and_log('G90')
        y_offset = -(PROBE_EDGE_OFFSET + tool_radius)
        await self._send_and_log(f'G10 L20 P1 Y{y_offset:.3f}')
        await self._log(f'Y set to {y_offset:.3f}mm')

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
