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

# Run probe_x (right edge)
self.edge_sign = 1
await run_sub_macro('probe_x.py')

# Apply X offset: add -50 to current position
x = self.grbl.status.wpos['x']
await self._send_and_log(f'G10 L20 P1 X{x - 50}')
await self._log(f'X offset: {x} -> {x - 50}')

# Run probe_y (front edge)
self.edge_sign = -1
await run_sub_macro('probe_y.py')

# Apply Y offset: add -20 to current position
y = self.grbl.status.wpos['y']
await self._send_and_log(f'G10 L20 P1 Y{y - 20}')
await self._log(f'Y offset: {y} -> {y - 20}')

# Run probe_z
await run_sub_macro('probe_z.py')

# Apply Z offset: add +26 to current position
z = self.grbl.status.wpos['z']
await self._send_and_log(f'G10 L20 P1 Z{z + 26}')
await self._log(f'Z offset: {z} -> {z + 26}')

await self._log('Centerline at (0, 0, 0)')
await self._log('=== CHUCK FIND COMPLETE ===')
