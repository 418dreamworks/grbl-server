# XY Check (Aircut)
# Finds bounding box of all G1 XY moves, traces rectangle at safe height
# Visual check - see cutting boundary

import re

await self._log('=== XY CHECK (AIRCUT) ===')

# Collect X and Y values from G1 moves
x_vals = []
y_vals = []
current_x, current_y = 0.0, 0.0
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

    # Extract X/Y values
    x_match = re.search(r'X([-\d.]+)', line, re.IGNORECASE)
    y_match = re.search(r'Y([-\d.]+)', line, re.IGNORECASE)

    if x_match:
        val = float(x_match.group(1))
        current_x = val if absolute_mode else current_x + val
    if y_match:
        val = float(y_match.group(1))
        current_y = val if absolute_mode else current_y + val

    # Only collect from G1 moves
    if re.match(r'G0*1\b', line, re.IGNORECASE):
        if x_match or y_match:
            x_vals.append(current_x)
            y_vals.append(current_y)

if not x_vals:
    await self._log('ERROR: No G1 XY moves found')
    return

# Bounding box
x_min, x_max = min(x_vals), max(x_vals)
y_min, y_max = min(y_vals), max(y_vals)

await self._log(f'Bounding box: X[{x_min:.3f}, {x_max:.3f}] Y[{y_min:.3f}, {y_max:.3f}]')

# WARNING: Traces at current Z height - no Z change, no spindle
current_z = self.grbl.status['wpos']['z']
await self._log(f'WARNING: Tracing at current Z={current_z:.3f}mm - ensure clearance!')
await self._wait_for_continue()

# Trace bounding box rectangle (no spindle, no Z change)
await self._log('Tracing bounding box...')
await self._send_and_log(f'G0 X{x_min:.3f} Y{y_min:.3f}')
await self._wait_idle()
await self._send_and_log(f'G0 X{x_max:.3f} Y{y_min:.3f}')
await self._wait_idle()
await self._send_and_log(f'G0 X{x_max:.3f} Y{y_max:.3f}')
await self._wait_idle()
await self._send_and_log(f'G0 X{x_min:.3f} Y{y_max:.3f}')
await self._wait_idle()
await self._send_and_log(f'G0 X{x_min:.3f} Y{y_min:.3f}')
await self._wait_idle()

await self._log('=== XY CHECK COMPLETE ===')
