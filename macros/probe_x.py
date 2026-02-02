# X Edge Probe
# edge_sign set by UI: -1=LEFT, +1=RIGHT
# Tracks displacement to return to start position after zeroing

total = 0.0
r = self.tool_diameter / 2

await self._log('=== X PROBE START ===')
await self._send_and_log('G91')  # relative mode

# 1. Clear probe surface
clear = self.edge_sign * (6 + r)
await self._send_and_log(f'G0 X{clear}')
await self._wait_idle()
total += clear

# 2. Plunge with safety check
await self._send_and_log('G38.3 Z-6 F100')
await self._wait_idle()
if self.grbl.last_probe['success']:
    await self._send_and_log('G0 Z6')
    await self._send_and_log(f'G0 X{-total}')
    await self._send_and_log('G90')
    await self._log('ERROR: Unexpected probe contact during plunge')
    return

# 3. First probe (fast) - track displacement
pre_x = self.grbl.status.wpos['x']
await self._send_and_log(f'G38.3 X{-self.edge_sign * 6} F50')
await self._wait_idle()

if not self.grbl.last_probe['success']:
    await self._send_and_log('G0 Z6')
    await self._send_and_log(f'G0 X{-total}')
    await self._send_and_log('G90')
    await self._log('ERROR: No probe contact')
    return

total += (self.grbl.status.wpos['x'] - pre_x)

# 4. Back off and refine
await self._send_and_log(f'G0 X{self.edge_sign * 1}')
await self._send_and_log(f'G38.3 X{-self.edge_sign * 2} F10')
await self._wait_idle()

# 5. Set coordinate (absolute) - sign depends on edge!
await self._send_and_log('G90')
await self._send_and_log(f'G10 L20 P1 X{self.edge_sign * (7 + r)}')

# 6. Return to start (relative) - back off first to avoid rubbing on Z raise
await self._send_and_log('G91')
await self._send_and_log(f'G0 X{total/5}')
await self._send_and_log('G0 Z6')
await self._send_and_log(f'G0 X{-total*6/5}')
await self._send_and_log('G90')

await self._log(f'X set to {self.edge_sign * (7 + r):.3f}mm')
await self._log('=== X PROBE COMPLETE ===')
