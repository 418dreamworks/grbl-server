# Line Contour - Unified Slot + Facing
# Inputs: end_x, end_y (from self.end_x, self.end_y), width (self.width), depth (self.depth)
# When width=0: Line Slot mode - ramp back and forth to depth
# When width>0: Zigzag facing mode - ramp entry then zigzag at each depth level
# Right-hand rule for perpendicular direction: (-end_y, end_x) / |line|

import asyncio
import math
import sys
import os
sys.path.insert(0, os.path.dirname(macro_dir))
from config import DOC_RATIO, PITCH_RATIO, STEPOVER_RATIO, FEED_CUT, SPINDLE_RPM, SPINDLE_WARMUP

doc = self.tool_diameter * DOC_RATIO
ramp_increment = self.tool_diameter * PITCH_RATIO
stepover = self.tool_diameter * STEPOVER_RATIO

# Calculate line length and perpendicular direction
line_length = math.sqrt(self.end_x**2 + self.end_y**2)
perp_unit_x = -self.end_y / line_length
perp_unit_y = self.end_x / line_length

if self.width == 0:
    await self._log(f'=== LINE SLOT START: ({self.end_x},{self.end_y}), depth={self.depth}mm ===')
else:
    await self._log(f'=== LINE CONTOUR START: ({self.end_x},{self.end_y}), width={self.width}mm, depth={self.depth}mm ===')

await self._send_and_log('G91')
await self._send_and_log(f'M3 S{SPINDLE_RPM}')
await asyncio.sleep(SPINDLE_WARMUP)

if self.width == 0:
    # LINE SLOT MODE: ramp back and forth to depth
    current_depth = 0
    at_end = False

    while current_depth < self.depth:
        descend = min(ramp_increment, self.depth - current_depth)

        if at_end:
            await self._send_and_log(f'G1 X{-self.end_x} Y{-self.end_y} Z{-descend} F{FEED_CUT}')
            at_end = False
        else:
            await self._send_and_log(f'G1 X{self.end_x} Y{self.end_y} Z{-descend} F{FEED_CUT}')
            at_end = True
        await self._wait_idle()

        current_depth += descend

    # Cleanup pass at full depth
    if at_end:
        await self._send_and_log(f'G1 X{-self.end_x} Y{-self.end_y} F{FEED_CUT}')
    else:
        await self._send_and_log(f'G1 X{self.end_x} Y{self.end_y} F{FEED_CUT}')
        at_end = True

    # Return to start
    if at_end:
        await self._send_and_log(f'G0 X{-self.end_x} Y{-self.end_y}')

else:
    # ZIGZAG FACING MODE: ramp entry then zigzag at each depth level
    current_depth = 0

    while current_depth < self.depth:
        level_depth = min(doc, self.depth - current_depth)
        ramp_per_pass = level_depth / 2
        at_end = False

        # Ramp entry (3 passes)
        await self._send_and_log(f'G1 X{self.end_x} Y{self.end_y} Z{-ramp_per_pass} F{FEED_CUT}')
        await self._send_and_log(f'G1 X{-self.end_x} Y{-self.end_y} Z{-ramp_per_pass} F{FEED_CUT}')
        await self._send_and_log(f'G1 X{self.end_x} Y{self.end_y} F{FEED_CUT}')
        at_end = True
        await self._wait_idle()

        current_depth += level_depth

        # Zigzag at this level
        covered = 0
        while covered < self.width:
            step = min(stepover, self.width - covered)
            px = perp_unit_x * step
            py = perp_unit_y * step
            await self._send_and_log(f'G1 X{px} Y{py} F{FEED_CUT}')
            covered += step

            if at_end:
                await self._send_and_log(f'G1 X{-self.end_x} Y{-self.end_y} F{FEED_CUT}')
                at_end = False
            else:
                await self._send_and_log(f'G1 X{self.end_x} Y{self.end_y} F{FEED_CUT}')
                at_end = True
            await self._wait_idle()

        await self._log(f'Level {current_depth:.2f}mm complete')

        # Return to start position for next level
        return_px = -perp_unit_x * self.width
        return_py = -perp_unit_y * self.width
        if at_end:
            await self._send_and_log(f'G0 X{-self.end_x + return_px} Y{-self.end_y + return_py}')
        else:
            await self._send_and_log(f'G0 X{return_px} Y{return_py}')

# Retract
await self._send_and_log(f'G0 Z{self.depth + 2}')
await self._send_and_log('M5')
await self._send_and_log('G90')
await self._log('=== LINE CONTOUR COMPLETE ===')
