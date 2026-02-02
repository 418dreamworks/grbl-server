# OD Contour - Circular Pocket with Helical Plunge + Spiral
# Inputs: start_dia, end_dia, depth (from self.start_dia, self.end_dia, self.depth)
# Optional: rapid_plunge (from self.rapid_plunge) - if True, single helix to full depth
# Current position = CENTER of circle
# Direction: start_dia < end_dia = spiral OUT, start_dia > end_dia = spiral IN
# When start_dia == end_dia: Just helical descent (circular slot)
# rapid_plunge=True: Center is clear, safe to helix to full depth in one pass
# rapid_plunge=False: Solid material, DOC-limited passes
# Uses G90 (absolute) throughout for accuracy

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(macro_dir))
from config import DOC_RATIO, PITCH_RATIO, STEPOVER_RATIO, SPINDLE_RPM, SPINDLE_WARMUP, feed_for_tool

doc = self.tool_diameter * DOC_RATIO
pitch = self.tool_diameter * PITCH_RATIO
stepover = self.tool_diameter * STEPOVER_RATIO
feed = feed_for_tool(self.tool_diameter)

# Rapid plunge option - single pass to full depth (center already clear)
rapid_plunge = getattr(self, 'rapid_plunge', False)

start_radius = self.start_dia / 2
end_radius = self.end_dia / 2

# Direction: +1 = spiral OUT, -1 = spiral IN, 0 = slot only
if end_radius > start_radius:
    step_sign = 1
    mode = 'OUT'
elif end_radius < start_radius:
    step_sign = -1
    mode = 'IN'
else:
    step_sign = 0
    mode = 'SLOT'

plunge_mode = 'RAPID' if rapid_plunge else 'NORMAL'
await self._log(f'=== OD CONTOUR START: {self.start_dia}mm → {self.end_dia}mm ({mode}), depth={self.depth}mm, plunge={plunge_mode} ===')

# Save current distance mode for restoration
original_mode = await self._get_distance_mode()

# Record start position (center of circle)
await self._wait_idle()
center_x = self.grbl.status.wpos['x']
center_y = self.grbl.status.wpos['y']
start_z = self.grbl.status.wpos['z']
target_z = start_z - self.depth

# Use absolute mode throughout
await self._send_and_log('G90')

await self._send_and_log(f'M3 S{SPINDLE_RPM}')
await asyncio.sleep(SPINDLE_WARMUP)

if rapid_plunge:
    # RAPID PLUNGE MODE: Single helix to full depth, then one spiral pass
    # (Use when center is already clear)

    # Move to start radius (+X from center)
    await self._send_and_log(f'G0 X{center_x + start_radius:.3f}')

    # Helical plunge to full depth
    current_z = start_z
    while current_z > target_z:
        descend = min(pitch, current_z - target_z)
        current_z -= descend
        await self._send_and_log(f'G3 X{center_x + start_radius:.3f} Y{center_y:.3f} Z{current_z:.3f} I{-start_radius:.3f} J0 F{feed:.0f}')
    await self._wait_idle()

    if step_sign != 0:
        # Single spiral to end radius
        current_radius = start_radius

        while (step_sign > 0 and current_radius < end_radius) or \
              (step_sign < 0 and current_radius > end_radius):
            # Full circle at current radius
            await self._send_and_log(f'G3 X{center_x + current_radius:.3f} Y{center_y:.3f} I{-current_radius:.3f} J0 F{feed:.0f}')
            # Step toward target
            remaining = abs(end_radius - current_radius)
            step = min(stepover, remaining)
            current_radius += step_sign * step
            await self._send_and_log(f'G1 X{center_x + current_radius:.3f} F{feed:.0f}')
            await self._wait_idle()

        # Cleanup circle at end radius
        await self._send_and_log(f'G3 X{center_x + current_radius:.3f} Y{center_y:.3f} I{-current_radius:.3f} J0 F{feed:.0f}')
    else:
        # SLOT MODE: just one cleanup circle
        current_radius = start_radius
        await self._send_and_log(f'G3 X{center_x + current_radius:.3f} Y{center_y:.3f} I{-current_radius:.3f} J0 F{feed:.0f}')

    await self._log(f'Rapid plunge complete at {self.depth:.2f}mm')

else:
    # NORMAL MODE: Multiple passes with DOC limit
    # (Use when cutting into solid material)
    current_z = start_z

    while current_z > target_z:
        level_depth = min(doc, current_z - target_z)
        level_target_z = current_z - level_depth

        # Move to start radius (+X from center)
        await self._send_and_log(f'G0 X{center_x + start_radius:.3f}')

        # Helical plunge at start_radius
        while current_z > level_target_z:
            descend = min(pitch, current_z - level_target_z)
            current_z -= descend
            await self._send_and_log(f'G3 X{center_x + start_radius:.3f} Y{center_y:.3f} Z{current_z:.3f} I{-start_radius:.3f} J0 F{feed:.0f}')
        await self._wait_idle()

        if step_sign != 0:
            # Spiral horizontally toward end_radius (Z fixed)
            current_radius = start_radius

            while (step_sign > 0 and current_radius < end_radius) or \
                  (step_sign < 0 and current_radius > end_radius):
                # Full circle at current radius
                await self._send_and_log(f'G3 X{center_x + current_radius:.3f} Y{center_y:.3f} I{-current_radius:.3f} J0 F{feed:.0f}')
                # Step toward target
                remaining = abs(end_radius - current_radius)
                step = min(stepover, remaining)
                current_radius += step_sign * step
                await self._send_and_log(f'G1 X{center_x + current_radius:.3f} F{feed:.0f}')
                await self._wait_idle()

            # Cleanup circle at end radius
            await self._send_and_log(f'G3 X{center_x + current_radius:.3f} Y{center_y:.3f} I{-current_radius:.3f} J0 F{feed:.0f}')
        else:
            # SLOT MODE: just one cleanup circle at same radius
            current_radius = start_radius
            await self._send_and_log(f'G3 X{center_x + current_radius:.3f} Y{center_y:.3f} I{-current_radius:.3f} J0 F{feed:.0f}')

        await self._log(f'Level z={current_z:.2f}mm complete')

        # Return to center for next level
        await self._send_and_log(f'G0 X{center_x:.3f}')

# Return to start position: Z first, then XY
await self._send_and_log('M5')
await self._send_and_log(f'G0 Z{start_z:.3f}')
await self._send_and_log(f'G0 X{center_x:.3f} Y{center_y:.3f}')

# Restore original distance mode
await self._send_and_log(original_mode)
await self._log('=== OD CONTOUR COMPLETE ===')
