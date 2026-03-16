# Rotary Turning - Cylinder/Cone from stock
# Uses side-cutting with ball end mill for best finish
# Inputs: tool_diameter, stock_shape, stock_dim, d_start, x_start, d_end, x_end

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(macro_dir))
from config import DOC_RATIO, SPINDLE_RPM, SPINDLE_WARMUP, feed_for_tool
import math

tool_r = self.tool_diameter / 2
doc = self.tool_diameter * DOC_RATIO        # Radial DOC per pass
feed = getattr(self, 'feed_override', None) or feed_for_tool(self.tool_diameter)
workpiece_rpm = getattr(self, 'workpiece_rpm', 60)
feed_per_rev = getattr(self, 'feed_per_rev', 0.2)

# Stock clearance (from UI prompts)
if self.stock_shape == 'square':
    stock_radius = self.stock_dim * math.sqrt(2) / 2
else:
    stock_radius = self.stock_dim / 2

# Final profile
r_start = self.d_start / 2
r_end = self.d_end / 2
length = abs(self.x_end - self.x_start)

# Validate
if length < 0.001:
    await self._log('ERROR: X_start and X_end are the same')
    return

if stock_radius < r_start or stock_radius < r_end:
    await self._log('ERROR: Stock radius smaller than final radius')
    return

# Calculate passes
max_removal = max(stock_radius - r_start, stock_radius - r_end)
num_passes = math.ceil(max_removal / doc)

# Feed calculation based on workpiece rotation
# A axis rotates, we want feed_per_rev mm of X travel per revolution
rotations = length / feed_per_rev
a_per_pass = rotations * 360
# Feed rate: travel length over time = length / (rotations / workpiece_rpm) mm/min
feed_rate = length / (rotations / workpiece_rpm) if rotations > 0 else feed

# Plunge depth: ball radius (tool engages at side)
plunge_z = -tool_r

# Record start position
await self._wait_idle()
start_x = self.grbl.status.wpos['x']
start_y = self.grbl.status.wpos['y']
start_z = self.grbl.status.wpos['z']
start_a = self.grbl.status.wpos['a']

await self._log(f'=== ROTARY TURNING ===')
await self._log(f'Stock: {self.stock_shape} -> radius={stock_radius:.3f}mm')
await self._log(f'Final: D{self.d_start:.1f} -> D{self.d_end:.1f} over {length:.1f}mm')
await self._log(f'Passes: {num_passes}, DOC: {doc:.2f}mm radial')
await self._log(f'Rotations/pass: {rotations:.1f}, A/pass: {a_per_pass:.0f} deg')

# Save and set mode
original_mode = await self._get_distance_mode()
await self._send_and_log('G90')
await self._send_and_log(f'M3 S{SPINDLE_RPM}')
await asyncio.sleep(SPINDLE_WARMUP)

# Cutting loop (bidirectional)
current_a = start_a

for pass_num in range(num_passes):
    pass_depth = (pass_num + 1) * doc
    # Calculate cut radius at each end for this pass
    # Don't cut below final radius
    cut_r_start = max(r_start, stock_radius - pass_depth)
    cut_r_end = max(r_end, stock_radius - pass_depth)

    # Y = cut_radius + tool_radius (side milling offset)
    y_at_start = cut_r_start + tool_r
    y_at_end = cut_r_end + tool_r

    await self._log(f'Pass {pass_num + 1}/{num_passes}: R={cut_r_start:.2f} -> {cut_r_end:.2f}')

    target_a = current_a + a_per_pass

    if pass_num % 2 == 0:
        # Forward pass: x_start -> x_end
        await self._send_and_log(f'G0 X{self.x_start:.3f}')
        await self._send_and_log(f'G0 Y{y_at_start:.3f}')
        await self._send_and_log(f'G1 Z{plunge_z:.3f} F300')
        await self._send_and_log(f'G1 X{self.x_end:.3f} Y{y_at_end:.3f} A{target_a:.3f} F{feed_rate:.0f}')
    else:
        # Reverse pass: x_end -> x_start
        await self._send_and_log(f'G0 X{self.x_end:.3f}')
        await self._send_and_log(f'G0 Y{y_at_end:.3f}')
        await self._send_and_log(f'G1 Z{plunge_z:.3f} F300')
        await self._send_and_log(f'G1 X{self.x_start:.3f} Y{y_at_start:.3f} A{target_a:.3f} F{feed_rate:.0f}')

    await self._wait_idle()
    await self._send_and_log('G0 Z0')  # Retract Z
    current_a = target_a

# Spindle off, return to start
await self._send_and_log('M5')
await self._send_and_log(f'G0 Z{start_z:.3f}')
await self._send_and_log(f'G0 Y{start_y:.3f}')
await self._send_and_log(f'G0 X{start_x:.3f}')
# A axis accumulates, no unwind

# Restore original mode
await self._send_and_log(original_mode)
await self._log('=== TURNING COMPLETE ===')
