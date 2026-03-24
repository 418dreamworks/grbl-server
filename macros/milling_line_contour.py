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

# Record start position for return
await self._wait_idle()
start_x = self.grbl.status.wpos['x']
start_y = self.grbl.status.wpos['y']
start_z = self.grbl.status.wpos['z']

# Start spindle
await self._send_and_log(f'M3 S{SPINDLE_RPM}')
await asyncio.sleep(SPINDLE_WARMUP)

# Use incremental mode for all cuts
await self._send_and_log('G91')

# Phase 1: Ramp to depth (zigzag along line while descending)
await self._log('Ramping to depth...')
remaining_depth = self.depth
at_end = False

ramp_lines = []
while remaining_depth > 0:
    descend = min(ramp_increment, remaining_depth)
    remaining_depth -= descend

    if at_end:
        ramp_lines.append(f'G1 X{-self.end_x:.3f} Y{-self.end_y:.3f} Z{-descend:.3f} F{feed:.0f}')
        at_end = False
    else:
        ramp_lines.append(f'G1 X{self.end_x:.3f} Y{self.end_y:.3f} Z{-descend:.3f} F{feed:.0f}')
        at_end = True

# Cleanup pass at full depth (no Z change)
if at_end:
    ramp_lines.append(f'G1 X{-self.end_x:.3f} Y{-self.end_y:.3f} F{feed:.0f}')
    at_end = False
else:
    ramp_lines.append(f'G1 X{self.end_x:.3f} Y{self.end_y:.3f} F{feed:.0f}')
    at_end = True

await self._stream_lines(ramp_lines)
await self._wait_idle()

# Phase 2: Zigzag to cover width (perpendicular steps)
total_perp_x = 0
total_perp_y = 0
covered = 0
zigzag_lines = []

while covered < self.width:
    step = min(stepover, self.width - covered)
    step_x = perp_unit_x * step
    step_y = perp_unit_y * step

    zigzag_lines.append(f'G1 X{step_x:.3f} Y{step_y:.3f} F{feed:.0f}')
    total_perp_x += step_x
    total_perp_y += step_y
    covered += step

    if at_end:
        zigzag_lines.append(f'G1 X{-self.end_x:.3f} Y{-self.end_y:.3f} F{feed:.0f}')
        at_end = False
    else:
        zigzag_lines.append(f'G1 X{self.end_x:.3f} Y{self.end_y:.3f} F{feed:.0f}')
        at_end = True

await self._stream_lines(zigzag_lines)
await self._wait_idle()

# Return to start: Z first, then XY
await self._send_and_log('M5')
await self._send_and_log('G90')
await self._send_and_log(f'G0 Z{start_z:.3f}')
await self._send_and_log(f'G0 X{start_x:.3f} Y{start_y:.3f}')

await self._send_and_log(original_mode)
await self._log('=== LINE CONTOUR COMPLETE ===')
