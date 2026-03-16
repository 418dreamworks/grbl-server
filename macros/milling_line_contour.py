# Line Contour - Tool center path, all relative moves (G91)
# No tool compensation - tool center moves exactly as specified
# Immune to coordinate system changes mid-cut

import asyncio
import math
import sys
import os
sys.path.insert(0, os.path.dirname(macro_dir))
from config import PITCH_RATIO, STEPOVER_RATIO, SPINDLE_RPM, SPINDLE_WARMUP, feed_for_tool

await self._log(f'=== LINE CONTOUR: dir=({self.end_x},{self.end_y}) width={self.width} depth={self.depth} tool={self.tool_diameter} ===')

ramp_increment = self.tool_diameter * PITCH_RATIO
stepover = self.tool_diameter * STEPOVER_RATIO
feed = getattr(self, 'feed_override', None) or feed_for_tool(self.tool_diameter)

# Calculate line length and perpendicular unit vector
line_length = math.sqrt(self.end_x**2 + self.end_y**2)
if line_length == 0:
    raise Exception('End point cannot be same as start point')

perp_unit_x = -self.end_y / line_length
perp_unit_y = self.end_x / line_length

# Save original mode
original_mode = await self._get_distance_mode()

await self._log(f'Ramp increment: {ramp_increment:.3f}, Stepover: {stepover:.3f}')

# Start spindle
await self._send_and_log(f'M3 S{SPINDLE_RPM}')
await asyncio.sleep(SPINDLE_WARMUP)

# Use incremental mode for all cuts
await self._send_and_log('G91')

# Phase 1: Ramp to depth (zigzag along line while descending)
await self._log('Ramping to depth...')
remaining_depth = self.depth
at_end = False

while remaining_depth > 0:
    descend = min(ramp_increment, remaining_depth)
    remaining_depth -= descend

    if at_end:
        # Cut back to start while descending
        await self._send_and_log(f'G1 X{-self.end_x:.3f} Y{-self.end_y:.3f} Z{-descend:.3f} F{feed:.0f}')
        at_end = False
    else:
        # Cut to end while descending
        await self._send_and_log(f'G1 X{self.end_x:.3f} Y{self.end_y:.3f} Z{-descend:.3f} F{feed:.0f}')
        at_end = True
    await self._wait_idle()

# Cleanup pass at full depth (no Z change)
if at_end:
    await self._send_and_log(f'G1 X{-self.end_x:.3f} Y{-self.end_y:.3f} F{feed:.0f}')
    at_end = False
else:
    await self._send_and_log(f'G1 X{self.end_x:.3f} Y{self.end_y:.3f} F{feed:.0f}')
    at_end = True
await self._wait_idle()

# Phase 2: Zigzag to cover width (perpendicular steps)
# Track total perpendicular displacement for return
total_perp_x = 0
total_perp_y = 0
covered = 0

while covered < self.width:
    step = min(stepover, self.width - covered)
    step_x = perp_unit_x * step
    step_y = perp_unit_y * step

    # Step perpendicular
    await self._send_and_log(f'G1 X{step_x:.3f} Y{step_y:.3f} F{feed:.0f}')
    await self._wait_idle()
    total_perp_x += step_x
    total_perp_y += step_y
    covered += step

    # Cut along line
    if at_end:
        await self._send_and_log(f'G1 X{-self.end_x:.3f} Y{-self.end_y:.3f} F{feed:.0f}')
        at_end = False
    else:
        await self._send_and_log(f'G1 X{self.end_x:.3f} Y{self.end_y:.3f} F{feed:.0f}')
        at_end = True
    await self._wait_idle()

# Return to start position
await self._send_and_log('M5')
await self._send_and_log(f'G0 Z{self.depth:.3f}')  # Back up to start Z

# Return XY: undo perpendicular offset, then return along line if at end
if at_end:
    await self._send_and_log(f'G0 X{-self.end_x - total_perp_x:.3f} Y{-self.end_y - total_perp_y:.3f}')
else:
    await self._send_and_log(f'G0 X{-total_perp_x:.3f} Y{-total_perp_y:.3f}')

# Restore original mode
await self._send_and_log(original_mode)
await self._log('=== LINE CONTOUR COMPLETE ===')
