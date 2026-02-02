# Rotary Tailstock Square Check
# Probes Y to verify tailstock is aligned with chuck centerline

import os
macro_dir = os.path.dirname(__file__)

r = self.tool_diameter / 2
TAILSTOCK_OFFSET = 17.6  # mm from probe Y=0 to tailstock centerline
ALIGNMENT_TOLERANCE = 0.05

await self._log('=== TAILSTOCK SQUARE CHECK ===')
await self._log('NOTE: Run Chuck Find first to establish centerline reference')

# Record Y position (chuck coordinate system, Y=0 is chuck centerline)
recorded_y = self.grbl.status.wpos['y']
await self._log(f'Current Y (chuck coords): {recorded_y:.3f}mm')

# Run probe_y (front edge toward chuck)
self.edge_sign = -1
exec(compile(open(os.path.join(macro_dir, 'probe_y.py')).read(), 'probe_y.py', 'exec'))

# Get Y after probe_y (probe coordinate system)
current_y = self.grbl.status.wpos['y']

# Tailstock centerline is 17.6mm above probe Y=0
# Convert to chuck coords: tailstock_y = 17.6 + (recorded_y - current_y)
tailstock_y = TAILSTOCK_OFFSET + (recorded_y - current_y)

# Restore chuck coordinate system
await self._send_and_log(f'G10 L20 P1 Y{recorded_y}')

# Report deviation from chuck centerline (Y=0)
await self._log(f'Tailstock centerline: Y = {tailstock_y:.3f}mm (should be 0.00)')

if abs(tailstock_y) <= ALIGNMENT_TOLERANCE:
    await self._log('SQUARE: Tailstock aligned within 0.05mm')
else:
    tap_direction = 'BACK' if tailstock_y > 0 else 'FRONT'
    await self._log(f'TAP TAILSTOCK toward {tap_direction} by {abs(tailstock_y):.3f}mm')

await self._log('=== TAILSTOCK CHECK COMPLETE ===')
