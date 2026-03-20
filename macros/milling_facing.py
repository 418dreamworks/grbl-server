# Rectangle Facing with Ramp Entry
# All dimensions are TOOL CENTER movement
# length = tool center travel in X, width = tool center travel in Y
# User accounts for tool radius themselves

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(macro_dir))
from config import DOC_RATIO, STEPOVER_RATIO, SPINDLE_RPM, SPINDLE_WARMUP, feed_for_tool

doc = self.tool_diameter * DOC_RATIO
stepover = self.tool_diameter * STEPOVER_RATIO
feed = getattr(self, 'feed_override', None) or feed_for_tool(self.tool_diameter)

await self._log(f'=== FACING START: length={self.length} width={self.width} depth={self.depth} tool={self.tool_diameter} ===')

# Save current distance mode for restoration
original_mode = await self._get_distance_mode()

# Record start position (tool center)
await self._wait_idle()
start_x = self.grbl.status.wpos['x']
start_y = self.grbl.status.wpos['y']
start_z = self.grbl.status.wpos['z']

await self._log(f'User position: X={start_x:.3f} Y={start_y:.3f} Z={start_z:.3f}')
await self._log(f'Tool path: X to {start_x + self.length:.3f}, Y to {start_y + self.width:.3f}')

# Use absolute mode throughout
await self._send_and_log('G90')

await self._send_and_log(f'M3 S{SPINDLE_RPM}')
await asyncio.sleep(SPINDLE_WARMUP)

current_z = start_z
target_z_limit = start_z - self.depth

while current_z > target_z_limit:
    remaining_depth = self.depth - (start_z - current_z)
    level_depth = min(doc, remaining_depth)
    half_depth = level_depth / 2
    target_z = current_z - level_depth

    # Build all G-code for this depth level
    level_lines = []

    # === Ramp entry (3 passes) ===
    level_lines.append(f'G1 X{start_x + self.length:.3f} Z{current_z - half_depth:.3f} F{feed:.0f}')
    level_lines.append(f'G1 X{start_x:.3f} Z{target_z:.3f} F{feed:.0f}')
    level_lines.append(f'G1 X{start_x + self.length:.3f} F{feed:.0f}')

    current_z = target_z

    # === Zigzag ===
    current_y = start_y
    at_right = True

    while current_y < start_y + self.width:
        step = min(stepover, start_y + self.width - current_y)
        current_y += step
        level_lines.append(f'G1 Y{current_y:.3f} F{feed:.0f}')
        if at_right:
            level_lines.append(f'G1 X{start_x:.3f} F{feed:.0f}')
            at_right = False
        else:
            level_lines.append(f'G1 X{start_x + self.length:.3f} F{feed:.0f}')
            at_right = True

    # Return to start corner
    if at_right:
        level_lines.append(f'G0 X{start_x:.3f}')
    level_lines.append(f'G0 Y{start_y:.3f}')

    # Stream entire level with character-counting
    await self._log(f'Streaming {len(level_lines)} lines for z={current_z:.2f}mm')
    await self._stream_lines(level_lines)
    await self._wait_idle()
    await self._log(f'Level z={current_z:.2f}mm complete')

# Return to start position: Z first, then XY
await self._send_and_log('M5')
await self._send_and_log(f'G0 Z{start_z:.3f}')
await self._send_and_log(f'G0 X{start_x:.3f} Y{start_y:.3f}')

# Restore original distance mode
await self._send_and_log(original_mode)
await self._log('=== FACING COMPLETE ===')
