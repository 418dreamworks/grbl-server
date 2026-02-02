# Rotary Chuck Find
# Runs probe_x, probe_y, probe_z, then shifts origin by chuck offsets

import os
# macro_dir is provided by MacroEngine namespace

r = self.tool_diameter / 2

# Helper to run a sub-macro (wraps in async function like MacroEngine does)
async def run_sub_macro(filename):
    code = open(os.path.join(macro_dir, filename)).read()
    ns = {'self': self, 'asyncio': asyncio, 'math': math, 'macro_dir': macro_dir}
    wrapped = "async def _sub():\n" + '\n'.join('    ' + line for line in code.split('\n'))
    exec(wrapped, ns)
    await ns['_sub']()

await self._log('=== CHUCK FIND ===')

# Run probe_x (right edge, edge_sign=1)
# probe_x sets X = 7+r at contact point
self.edge_sign = 1
await run_sub_macro('probe_x.py')

# Apply X offset: probe sets X = 7+r, chuck centerline is 50mm from probe edge
# New X at probe contact = (7+r) - 50
probe_x_value = 7 + r
await self._send_and_log(f'G10 L20 P1 X{probe_x_value - 50}')
await self._log(f'X: probe={probe_x_value:.3f}, chuck={probe_x_value - 50:.3f}')

# Run probe_y (front edge, edge_sign=-1)
# probe_y sets Y = -(7+r) at contact point
self.edge_sign = -1
await run_sub_macro('probe_y.py')

# Apply Y offset: probe sets Y = -(7+r), chuck centerline is 20mm from probe edge
# New Y at probe contact = -(7+r) - 20
probe_y_value = -(7 + r)
await self._send_and_log(f'G10 L20 P1 Y{probe_y_value - 20}')
await self._log(f'Y: probe={probe_y_value:.3f}, chuck={probe_y_value - 20:.3f}')

# Run probe_z
# probe_z sets Z = 22 at contact point (plate thickness)
await run_sub_macro('probe_z.py')

# Apply Z offset: probe sets Z = 22, chuck surface is 26mm above probe plate
# New Z at probe contact = 22 + 26 = 48
probe_z_value = 22
await self._send_and_log(f'G10 L20 P1 Z{probe_z_value + 26}')
await self._log(f'Z: probe={probe_z_value:.3f}, chuck={probe_z_value + 26:.3f}')

await self._log('Chuck centerline at (0, 0, 0)')
await self._log('=== CHUCK FIND COMPLETE ===')
