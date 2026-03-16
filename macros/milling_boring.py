# Boring Cycle with Helical Plunge and Spiral Outward
# Inputs: bore_dia (from self.bore_dia), depth (from self.depth)
# tool_dia from self.tool_diameter
# DOC, Helical pitch, Stepover all from config ratios
# Uses G90 (absolute) throughout for accuracy

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(macro_dir))
from config import DOC_RATIO, PITCH_RATIO, STEPOVER_RATIO, HELIX_START_RADIUS, FINISH_STOCK, SPINDLE_RPM, SPINDLE_WARMUP, feed_for_tool

doc = self.tool_diameter * DOC_RATIO
pitch = self.tool_diameter * PITCH_RATIO
stepover = self.tool_diameter * STEPOVER_RATIO
helix_radius = HELIX_START_RADIUS
finish_stock = FINISH_STOCK
tool_radius = self.tool_diameter / 2
bore_radius = self.bore_dia / 2 - tool_radius  # Tool path radius (tool edge cuts at bore_dia/2)
feed = getattr(self, 'feed_override', None) or feed_for_tool(self.tool_diameter)

await self._log(f'=== BORE START: dia={self.bore_dia}mm, depth={self.depth}mm ===')

# Validation
if self.bore_dia < self.tool_diameter + 0.1:
    await self._log('ERROR: Bore diameter too small. Use smaller bit or Drill macro.')
    return

# Save current distance mode for restoration
original_mode = await self._get_distance_mode()

# Record start position (center of bore)
await self._wait_idle()
center_x = self.grbl.status.wpos['x']
center_y = self.grbl.status.wpos['y']
start_z = self.grbl.status.wpos['z']

# Use absolute mode throughout
await self._send_and_log('G90')

await self._send_and_log(f'M3 S{SPINDLE_RPM}')
await asyncio.sleep(SPINDLE_WARMUP)

current_z = start_z  # Track Z as we descend

target_z_limit = start_z - self.depth  # Never go below this

while current_z > target_z_limit:
    remaining_depth = self.depth - (start_z - current_z)
    level_depth = min(doc, remaining_depth)
    target_z = current_z - level_depth

    # Move to helix start position (+X from center)
    await self._send_and_log(f'G0 X{center_x + helix_radius:.3f}')

    # Helical plunge at helix_radius
    plunge_z = current_z
    while plunge_z > target_z:
        descend = min(pitch, plunge_z - target_z)
        plunge_z -= descend
        # Arc I/J are always relative to current position
        await self._send_and_log(f'G3 X{center_x + helix_radius:.3f} Y{center_y:.3f} Z{plunge_z:.3f} I{-helix_radius:.3f} J0 F{feed:.0f}')
    await self._wait_idle()

    current_z = target_z

    # Determine target radius for this level
    depth_so_far = start_z - current_z
    if depth_so_far >= self.depth:
        target_radius = bore_radius  # Final pass - full diameter
    else:
        target_radius = bore_radius - finish_stock  # Leave finish stock

    # Spiral outward from helix_radius to target_radius
    current_radius = helix_radius
    while current_radius < target_radius:
        step = min(stepover, target_radius - current_radius)
        current_radius += step
        # Step outward in +X
        await self._send_and_log(f'G1 X{center_x + current_radius:.3f} F{feed:.0f}')
        # Full circle at new radius
        await self._send_and_log(f'G3 X{center_x + current_radius:.3f} Y{center_y:.3f} I{-current_radius:.3f} J0 F{feed:.0f}')
        await self._wait_idle()

    await self._log(f'Level z={current_z:.2f}mm complete, radius={current_radius:.3f}mm')

    # Return to center for next level
    await self._send_and_log(f'G0 X{center_x:.3f}')

# Return to start position: Z first, then XY
await self._send_and_log('M5')
await self._send_and_log(f'G0 Z{start_z:.3f}')
await self._send_and_log(f'G0 X{center_x:.3f} Y{center_y:.3f}')

# Restore original distance mode
await self._send_and_log(original_mode)
await self._log('=== BORE COMPLETE ===')
