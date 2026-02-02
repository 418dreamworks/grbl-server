# Z Check (Depth Verification)
# Finds max Z depth in G-code, plunges to that depth and back
# Visual check - verify clearance at cutting depth

import re

await self._log('=== Z CHECK (DEPTH) ===')

# Parse G-code for Z values, accounting for G90/G91
z_values = []
current_z = 0.0
absolute_mode = True  # G90 default

for line in self.loaded_gcode.splitlines():
    line = line.split(';')[0].strip()
    if not line:
        continue

    # Track G90/G91 mode
    if re.search(r'\bG90\b', line, re.IGNORECASE):
        absolute_mode = True
    if re.search(r'\bG91\b', line, re.IGNORECASE):
        absolute_mode = False

    # Find Z values
    match = re.search(r'Z([-\d.]+)', line, re.IGNORECASE)
    if match:
        val = float(match.group(1))
        current_z = val if absolute_mode else current_z + val
        z_values.append(current_z)

if not z_values:
    await self._log('ERROR: No Z values found in G-code')
    return

z_min = min(z_values)
z_max = max(z_values)
start_z = self.grbl.status.wpos['z']

await self._log(f'G-code Z range: [{z_min:.3f}, {z_max:.3f}]')
await self._log(f'Max depth: {z_min:.3f}mm')
await self._log(f'Current Z: {start_z:.3f}mm')
await self._log('Plunging at current XY...')
await self._wait_for_continue()

# Plunge to lowest Z
await self._send_and_log('G90')
await self._send_and_log(f'G1 Z{z_min:.3f} F100')
await self._wait_idle()

await self._log(f'At max depth Z{z_min:.3f}')
await self._wait_for_continue()

# Return to start
await self._send_and_log(f'G0 Z{start_z:.3f}')
await self._wait_idle()

await self._log('=== Z CHECK COMPLETE ===')
