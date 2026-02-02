# Rotary Chuck Find
# Runs probe_x, probe_y, probe_z, then shifts origin by chuck offsets
# Returns to start position at end

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

# Record start MPos (WPos shifts during macro, MPos is stable)
await self._wait_idle()
start_mpos_x = self.grbl.status.mpos['x']
start_mpos_y = self.grbl.status.mpos['y']
start_mpos_z = self.grbl.status.mpos['z']

# Run probe_x (right edge)
self.edge_sign = 1
await run_sub_macro('probe_x.py')
await self._wait_idle()

# Apply X offset: shift coordinate system by -50
x = self.grbl.status.wpos['x']
await self._send_and_log(f'G10 L20 P1 X{x - 50}')
await self._log(f'X offset: {x:.3f} -> {x - 50:.3f}')

# Run probe_y (front edge)
self.edge_sign = -1
await run_sub_macro('probe_y.py')
await self._wait_idle()

# Apply Y offset: shift coordinate system by -20
y = self.grbl.status.wpos['y']
await self._send_and_log(f'G10 L20 P1 Y{y - 20}')
await self._log(f'Y offset: {y:.3f} -> {y - 20:.3f}')

# Run probe_z
await run_sub_macro('probe_z.py')
await self._wait_idle()

# Apply Z offset: shift coordinate system by +26
z = self.grbl.status.wpos['z']
await self._send_and_log(f'G10 L20 P1 Z{z + 26}')
await self._log(f'Z offset: {z:.3f} -> {z + 26:.3f}')

await self._log('Chuck centerline at (0, 0, 0)')

# Return to start position using MPos delta (Z first, then XY)
await self._wait_idle()
delta_x = start_mpos_x - self.grbl.status.mpos['x']
delta_y = start_mpos_y - self.grbl.status.mpos['y']
delta_z = start_mpos_z - self.grbl.status.mpos['z']

await self._send_and_log('G91')
await self._send_and_log(f'G0 Z{delta_z:.3f}')
await self._send_and_log(f'G0 X{delta_x:.3f} Y{delta_y:.3f}')
await self._send_and_log('G90')

await self._log('=== CHUCK FIND COMPLETE ===')
