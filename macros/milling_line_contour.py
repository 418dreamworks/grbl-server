# Line Contour - Slot or Multiple Parallel Slots
# Inputs: end_x, end_y (relative from start), width (perpendicular extent), depth
# When width=0: Single slot - ramp back and forth to full depth
# When width>0: Multiple parallel slots - complete each slot to full depth, step perpendicular, repeat
# Right-hand rule: thumb points in width direction when index finger points at end
# Uses G90 (absolute) throughout for accuracy

import asyncio
import math
import sys
import os
sys.path.insert(0, os.path.dirname(macro_dir))
from config import PITCH_RATIO, STEPOVER_RATIO, SPINDLE_RPM, SPINDLE_WARMUP, feed_for_tool

ramp_increment = self.tool_diameter * PITCH_RATIO
stepover = self.tool_diameter * STEPOVER_RATIO
feed = feed_for_tool(self.tool_diameter)

# Calculate line length and perpendicular unit vector (right-hand rule)
line_length = math.sqrt(self.end_x**2 + self.end_y**2)
if line_length == 0:
    await self._log('ERROR: End point cannot be same as start point')
    return
perp_unit_x = -self.end_y / line_length
perp_unit_y = self.end_x / line_length

if self.width == 0:
    await self._log(f'=== LINE SLOT START: ({self.end_x},{self.end_y}), depth={self.depth}mm ===')
else:
    await self._log(f'=== LINE CONTOUR START: ({self.end_x},{self.end_y}), width={self.width}mm, depth={self.depth}mm ===')

# Save current distance mode for restoration
original_mode = await self._get_distance_mode()

# Record start position
await self._wait_idle()
start_x = self.grbl.status.wpos['x']
start_y = self.grbl.status.wpos['y']
start_z = self.grbl.status.wpos['z']

# Calculate absolute end point
end_abs_x = start_x + self.end_x
end_abs_y = start_y + self.end_y
target_z = start_z - self.depth

# Use absolute mode throughout
await self._send_and_log('G90')

await self._send_and_log(f'M3 S{SPINDLE_RPM}')
await asyncio.sleep(SPINDLE_WARMUP)

# Function to cut one slot to full depth at given offset
async def cut_slot(slot_start_x, slot_start_y, slot_end_x, slot_end_y):
    """Ramp back and forth until full depth, then cleanup pass."""
    current_z = start_z
    at_end = False

    # Ramp to depth
    while current_z > target_z:
        descend = min(ramp_increment, current_z - target_z)
        next_z = current_z - descend

        if at_end:
            await self._send_and_log(f'G1 X{slot_start_x:.3f} Y{slot_start_y:.3f} Z{next_z:.3f} F{feed:.0f}')
            at_end = False
        else:
            await self._send_and_log(f'G1 X{slot_end_x:.3f} Y{slot_end_y:.3f} Z{next_z:.3f} F{feed:.0f}')
            at_end = True
        await self._wait_idle()
        current_z = next_z

    # Cleanup pass at full depth
    if at_end:
        await self._send_and_log(f'G1 X{slot_start_x:.3f} Y{slot_start_y:.3f} F{feed:.0f}')
        at_end = False
    else:
        await self._send_and_log(f'G1 X{slot_end_x:.3f} Y{slot_end_y:.3f} F{feed:.0f}')
        at_end = True
    await self._wait_idle()

    return at_end

if self.width == 0:
    # Single slot mode
    await cut_slot(start_x, start_y, end_abs_x, end_abs_y)
else:
    # Multiple parallel slots mode
    covered = 0
    slot_num = 0

    while covered <= self.width:
        # Calculate offset for this slot
        offset_x = perp_unit_x * covered
        offset_y = perp_unit_y * covered

        slot_start_x = start_x + offset_x
        slot_start_y = start_y + offset_y
        slot_end_x = end_abs_x + offset_x
        slot_end_y = end_abs_y + offset_y

        # Move to slot start (rapid at safe Z)
        if slot_num > 0:
            await self._send_and_log(f'G0 Z{start_z:.3f}')
            await self._send_and_log(f'G0 X{slot_start_x:.3f} Y{slot_start_y:.3f}')

        # Cut this slot to full depth
        await cut_slot(slot_start_x, slot_start_y, slot_end_x, slot_end_y)

        slot_num += 1
        await self._log(f'Slot {slot_num} complete at offset {covered:.2f}mm')

        # Step perpendicular
        if covered < self.width:
            covered += min(stepover, self.width - covered)
            if covered > self.width:
                covered = self.width
        else:
            break

# Return to start position: Z first, then XY
await self._send_and_log('M5')
await self._send_and_log(f'G0 Z{start_z:.3f}')
await self._send_and_log(f'G0 X{start_x:.3f} Y{start_y:.3f}')

# Restore original distance mode
await self._send_and_log(original_mode)
await self._log('=== LINE CONTOUR COMPLETE ===')
