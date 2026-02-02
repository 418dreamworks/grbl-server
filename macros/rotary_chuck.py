# Rotary Chuck Find
# Probes X (right), Y (front), Z and sets coordinates for centerline = (0,0,0)

r = self.tool_diameter / 2

# Chuck offsets: probed corner relative to centerline
CHUCK_X_OFFSET = -50
CHUCK_Y_OFFSET = -20
CHUCK_Z_OFFSET = 26

await self._log('=== CHUCK FIND ===')
await self._send_and_log('G91')

# X PROBE (right edge)
await self._send_and_log(f'G0 X{6 + r}')
await self._wait_idle()
await self._send_and_log('G38.3 Z-6 F100')
await self._wait_idle()
if self.grbl.last_probe['success']:
    await self._log('ERROR: Unexpected X plunge contact')
    return
await self._send_and_log('G38.3 X-6 F50')
await self._wait_idle()
if not self.grbl.last_probe['success']:
    await self._log('ERROR: No X contact')
    return
await self._send_and_log('G0 X1')
await self._send_and_log('G38.3 X-2 F10')
await self._wait_idle()
await self._send_and_log('G90')
await self._send_and_log(f'G10 L20 P1 X{CHUCK_X_OFFSET - r}')
await self._log(f'X set to {CHUCK_X_OFFSET - r:.3f}')
await self._send_and_log('G91')
await self._send_and_log('G0 Z6')
await self._wait_idle()

# Y PROBE (front edge)
await self._send_and_log(f'G0 Y{-(6 + r)}')
await self._wait_idle()
await self._send_and_log('G38.3 Z-6 F100')
await self._wait_idle()
if self.grbl.last_probe['success']:
    await self._log('ERROR: Unexpected Y plunge contact')
    return
await self._send_and_log('G38.3 Y6 F50')
await self._wait_idle()
if not self.grbl.last_probe['success']:
    await self._log('ERROR: No Y contact')
    return
await self._send_and_log('G0 Y-1')
await self._send_and_log('G38.3 Y2 F10')
await self._wait_idle()
await self._send_and_log('G90')
await self._send_and_log(f'G10 L20 P1 Y{CHUCK_Y_OFFSET + r}')
await self._log(f'Y set to {CHUCK_Y_OFFSET + r:.3f}')
await self._send_and_log('G91')
await self._send_and_log('G0 Z6')
await self._wait_idle()

# Z PROBE
await self._send_and_log('G38.2 Z-11 F50')
await self._wait_idle()
await self._send_and_log('G0 Z2.5')
await self._send_and_log('G38.2 Z-3 F10')
await self._wait_idle()
await self._send_and_log('G90')
await self._send_and_log(f'G10 L20 P1 Z{CHUCK_Z_OFFSET}')
await self._log(f'Z set to {CHUCK_Z_OFFSET}')

# Move to centerline
await self._send_and_log('G91')
await self._send_and_log('G0 Z20')
await self._send_and_log('G0 Y50')
await self._wait_idle()
await self._send_and_log('G90')

await self._log('Centerline at (0, 0, 0)')
await self._log('=== CHUCK FIND COMPLETE ===')
