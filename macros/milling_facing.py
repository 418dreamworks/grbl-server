# Rectangle Facing with Ramp Entry
# Inputs: length (from self.length), width (from self.width), depth (from self.depth)
# tool_dia from self.tool_diameter
# DOC = tool_dia * DOC_RATIO, Stepover = tool_dia * STEPOVER_RATIO
# Ramp entry avoids slotting (never full radial engagement)

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(macro_dir))
from config import DOC_RATIO, STEPOVER_RATIO, FEED_CUT, SPINDLE_RPM, SPINDLE_WARMUP

doc = self.tool_diameter * DOC_RATIO
stepover = self.tool_diameter * STEPOVER_RATIO
r = self.tool_diameter / 2

# Tool path dimensions (offset by tool radius on each side)
path_x = self.length - self.tool_diameter
path_y = self.width - self.tool_diameter

if path_x <= 0 or path_y <= 0:
    await self._log(f'ERROR: Pocket too small for tool. Min size: {self.tool_diameter:.1f}x{self.tool_diameter:.1f}mm')
    return

await self._log(f'=== FACING START: {self.length}x{self.width}mm pocket, depth={self.depth}mm ===')
await self._log(f'Tool: {self.tool_diameter}mm, Path: {path_x:.1f}x{path_y:.1f}mm')

# Record start position for return
await self._wait_idle()
start_x = self.grbl.status.wpos['x']
start_y = self.grbl.status.wpos['y']
start_z = self.grbl.status.wpos['z']

await self._send_and_log('G91')
await self._send_and_log(f'M3 S{SPINDLE_RPM}')
await asyncio.sleep(SPINDLE_WARMUP)

# Move to start corner (offset by radius so tool edge is at 0,0)
await self._send_and_log(f'G0 X{r:.3f} Y{r:.3f}')

current_depth = 0

while current_depth < self.depth:
    level_depth = min(self.depth - current_depth, doc)
    ramp_per_pass = level_depth / 2

    # === Ramp entry (3 passes) ===
    # Pass 1: 0→path_x, descend half
    await self._send_and_log(f'G1 X{path_x:.3f} Z{-ramp_per_pass:.3f} F{FEED_CUT}')

    # Pass 2: path_x→0, descend half
    await self._send_and_log(f'G1 X{-path_x:.3f} Z{-ramp_per_pass:.3f} F{FEED_CUT}')

    # Pass 3: 0→path_x, flat (cleanup)
    await self._send_and_log(f'G1 X{path_x:.3f} F{FEED_CUT}')
    await self._wait_idle()

    # Now at (path_x, 0) at new depth

    # === Zigzag ===
    y_pos = 0
    at_right = True

    while y_pos < path_y:
        # Step in Y
        step = min(stepover, path_y - y_pos)
        await self._send_and_log(f'G1 Y{step:.3f} F{FEED_CUT}')
        y_pos += step

        # Cut in X
        if at_right:
            await self._send_and_log(f'G1 X{-path_x:.3f} F{FEED_CUT}')
            at_right = False
        else:
            await self._send_and_log(f'G1 X{path_x:.3f} F{FEED_CUT}')
            at_right = True
        await self._wait_idle()

    current_depth += level_depth
    await self._log(f'Level {current_depth:.2f}mm complete')

    # Return to start corner for next depth level
    if at_right:
        await self._send_and_log(f'G0 X{-path_x:.3f}')
    await self._send_and_log(f'G0 Y{-path_y:.3f}')

# Return to start position: Z first, then XY
await self._send_and_log('M5')
await self._send_and_log('G90')
await self._send_and_log(f'G0 Z{start_z:.3f}')
await self._send_and_log(f'G0 X{start_x:.3f} Y{start_y:.3f}')
await self._log('=== FACING COMPLETE ===')
