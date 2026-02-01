# Y Edge Probe
# edge_sign set by UI: -1=FRONT, +1=BACK
# Tracks displacement to return to start position after zeroing

total = 0.0
r = self.tool_diameter / 2

await self._log('=== Y PROBE START ===')
await self._send_and_log('G91')  # relative mode

# 1. Clear probe surface
clear = self.edge_sign * (6 + r)
await self._send_and_log(f'G0 Y{clear}')
await self._wait_idle()
total += clear

# 2. Plunge with safety check
await self._send_and_log('G38.3 Z-6 F100')
await self._wait_idle()
if self.grbl.last_probe['success']:
    await self._send_and_log('G0 Z6')
    await self._send_and_log(f'G0 Y{-total}')
    await self._send_and_log('G90')
    await self._log('ERROR: Unexpected probe contact during plunge')
    return

# 3. First probe (fast) - track displacement
pre_y = self.grbl.status['wpos']['y']
await self._send_and_log(f'G38.3 Y{-self.edge_sign * 6} F50')
await self._wait_idle()

if not self.grbl.last_probe['success']:
    await self._send_and_log('G0 Z6')
    await self._send_and_log(f'G0 Y{-total}')
    await self._send_and_log('G90')
    await self._log('ERROR: No probe contact')
    return

total += (self.grbl.status['wpos']['y'] - pre_y)

# 4. Back off and refine
await self._send_and_log(f'G0 Y{self.edge_sign * 1}')
await self._send_and_log(f'G38.3 Y{-self.edge_sign * 2} F10')
await self._wait_idle()

# 5. Set coordinate (absolute) - sign depends on edge!
await self._send_and_log('G90')
await self._send_and_log(f'G10 L20 P1 Y{self.edge_sign * (7 + r)}')

# 6. Return to start (relative)
await self._send_and_log('G91')
await self._send_and_log(f'G0 Y{-total}')
await self._send_and_log('G0 Z6')
await self._send_and_log('G90')

await self._log(f'Y set to {self.edge_sign * (7 + r):.3f}mm')
await self._log('=== Y PROBE COMPLETE ===')
