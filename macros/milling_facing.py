# Rectangle Facing with Ramp Entry
# Inputs: length (from self.length), width (from self.width), depth (from self.depth)
# tool_dia from self.tool_diameter
# DOC = tool_dia * DOC_RATIO, Stepover = tool_dia * STEPOVER_RATIO
# Ramp entry avoids slotting (never full radial engagement)
# Uses G90 (absolute) throughout for accuracy
# User positions tool edge at lower-left corner of pocket

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(macro_dir))
from config import DOC_RATIO, STEPOVER_RATIO, SPINDLE_RPM, SPINDLE_WARMUP, feed_for_tool

doc = self.tool_diameter * DOC_RATIO
stepover = self.tool_diameter * STEPOVER_RATIO
r = self.tool_diameter / 2
feed = feed_for_tool(self.tool_diameter)

# Tool path dimensions (tool center travel distance)
path_x = self.length - self.tool_diameter
path_y = self.width - self.tool_diameter

if path_x <= 0 or path_y <= 0:
    await self._log(f'ERROR: Pocket too small for tool. Min size: {self.tool_diameter:.1f}x{self.tool_diameter:.1f}mm')
    return

await self._log(f'=== FACING START: {self.length}x{self.width}mm pocket, depth={self.depth}mm ===')
await self._log(f'Tool: {self.tool_diameter}mm, Path: {path_x:.1f}x{path_y:.1f}mm')

# Save current distance mode for restoration
original_mode = await self._get_distance_mode()

# Record start position (tool center, edge is at pocket corner)
await self._wait_idle()
start_x = self.grbl.status.wpos['x']
start_y = self.grbl.status.wpos['y']
start_z = self.grbl.status.wpos['z']

# Use absolute mode throughout
await self._send_and_log('G90')

await self._send_and_log(f'M3 S{SPINDLE_RPM}')
await asyncio.sleep(SPINDLE_WARMUP)

# No XY move needed - tool edge already at pocket corner

current_z = start_z

while current_z > start_z - self.depth:
    level_depth = min(doc, start_z - current_z + self.depth)
    half_depth = level_depth / 2
    target_z = current_z - level_depth

    # === Ramp entry (3 passes) ===
    # Pass 1: start→right, descend half
    await self._send_and_log(f'G1 X{start_x + path_x:.3f} Z{current_z - half_depth:.3f} F{feed:.0f}')

    # Pass 2: right→start, descend half
    await self._send_and_log(f'G1 X{start_x:.3f} Z{target_z:.3f} F{feed:.0f}')

    # Pass 3: start→right, flat (cleanup)
    await self._send_and_log(f'G1 X{start_x + path_x:.3f} F{feed:.0f}')
    await self._wait_idle()

    current_z = target_z

    # Now at (start_x + path_x, start_y, target_z)

    # === Zigzag ===
    current_y = start_y
    at_right = True  # We're at start_x + path_x

    while current_y < start_y + path_y:
        # Step in Y
        step = min(stepover, start_y + path_y - current_y)
        current_y += step
        await self._send_and_log(f'G1 Y{current_y:.3f} F{feed:.0f}')

        # Cut in X
        if at_right:
            await self._send_and_log(f'G1 X{start_x:.3f} F{feed:.0f}')
            at_right = False
        else:
            await self._send_and_log(f'G1 X{start_x + path_x:.3f} F{feed:.0f}')
            at_right = True
        await self._wait_idle()

    await self._log(f'Level z={current_z:.2f}mm complete')

    # Return to start corner for next depth level
    if at_right:
        await self._send_and_log(f'G0 X{start_x:.3f}')
    await self._send_and_log(f'G0 Y{start_y:.3f}')

# Return to start position: Z first, then XY
await self._send_and_log('M5')
await self._send_and_log(f'G0 Z{start_z:.3f}')
await self._send_and_log(f'G0 X{start_x:.3f} Y{start_y:.3f}')

# Restore original distance mode
await self._send_and_log(original_mode)
await self._log('=== FACING COMPLETE ===')
