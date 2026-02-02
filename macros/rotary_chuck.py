# Rotary Chuck Find
# Runs probe_x, probe_y, probe_z, then shifts origin by chuck offsets

import os
macro_dir = os.path.dirname(__file__)

r = self.tool_diameter / 2

await self._log('=== CHUCK FIND ===')

# Run probe_x (right edge)
self.edge_sign = 1
exec(compile(open(os.path.join(macro_dir, 'probe_x.py')).read(), 'probe_x.py', 'exec'))

# Apply X offset: add -50 to current position
x = self.grbl.status.wpos['x']
await self._send_and_log(f'G10 L20 P1 X{x - 50}')
await self._log(f'X offset: {x} -> {x - 50}')

# Run probe_y (front edge)
self.edge_sign = -1
exec(compile(open(os.path.join(macro_dir, 'probe_y.py')).read(), 'probe_y.py', 'exec'))

# Apply Y offset: add -20 to current position
y = self.grbl.status.wpos['y']
await self._send_and_log(f'G10 L20 P1 Y{y - 20}')
await self._log(f'Y offset: {y} -> {y - 20}')

# Run probe_z
exec(compile(open(os.path.join(macro_dir, 'probe_z.py')).read(), 'probe_z.py', 'exec'))

# Apply Z offset: add +26 to current position
z = self.grbl.status.wpos['z']
await self._send_and_log(f'G10 L20 P1 Z{z + 26}')
await self._log(f'Z offset: {z} -> {z + 26}')

await self._log('Centerline at (0, 0, 0)')
await self._log('=== CHUCK FIND COMPLETE ===')
