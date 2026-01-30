# Line Contour
# Cut a straight line from current position
# Edit parameters below before running

length = 50    # Line length (mm)
depth = 5      # Cut depth (mm)
direction = 'X'  # Direction: 'X', 'Y', or angle in degrees

import math

await self._log(f'=== LINE CONTOUR START: length={length}mm depth={depth}mm dir={direction} ===')

# Relative mode
await self._send_and_log('G91')

# Plunge
await self._send_and_log(f'G1 Z-{depth:.3f} F500')
await self._wait_idle()

# Cut line
if direction.upper() == 'X':
    await self._send_and_log(f'G1 X{length:.3f} F600')
elif direction.upper() == 'Y':
    await self._send_and_log(f'G1 Y{length:.3f} F600')
else:
    angle = float(direction) * math.pi / 180
    dx = length * math.cos(angle)
    dy = length * math.sin(angle)
    await self._send_and_log(f'G1 X{dx:.3f} Y{dy:.3f} F600')
await self._wait_idle()

# Retract
await self._send_and_log(f'G0 Z{depth:.3f}')
await self._wait_idle()

# Back to absolute
await self._send_and_log('G90')

await self._log('=== LINE CONTOUR COMPLETE ===')
