# Rotary Tailstock Square Check
# Probes Y on tailstock to verify square with chuck centerline
# Reports deviation - does NOT set coordinates
# Run Chuck Find first to establish centerline reference

r = self.tool_diameter / 2

# Tailstock: 17.6mm from centerline to probe edge
TAILSTOCK_EDGE_OFFSET = 7 + 21.2/2
ALIGNMENT_TOLERANCE = 0.05

await self._log('=== TAILSTOCK SQUARE CHECK ===')
await self._send_and_log('G91')

# Y PROBE (bottom edge - toward chuck)
await self._send_and_log(f'G0 Y{6 + r}')
await self._wait_idle()
await self._send_and_log('G38.3 Z-6 F100')
await self._wait_idle()
if self.grbl.last_probe['success']:
    await self._log('ERROR: Unexpected plunge contact')
    return
await self._send_and_log('G38.3 Y-6 F50')
await self._wait_idle()
if not self.grbl.last_probe['success']:
    await self._log('ERROR: No Y contact')
    return
await self._send_and_log('G0 Y1')
await self._send_and_log('G38.3 Y-2 F10')
await self._wait_idle()

# Read probed Y and calculate deviation
probed_y = self.grbl.status.wpos['y']
expected_y = -(r + TAILSTOCK_EDGE_OFFSET)
y_deviation = probed_y - expected_y

# Retract
await self._send_and_log('G0 Z6')
await self._send_and_log(f'G0 Y{-(6 + r)}')
await self._send_and_log('G90')
await self._wait_idle()

# Report
await self._log(f'Probed Y: {probed_y:.3f}mm (expected {expected_y:.3f}mm)')
await self._log(f'Deviation: {y_deviation:.3f}mm')

if abs(y_deviation) <= ALIGNMENT_TOLERANCE:
    await self._log('SQUARE: Within 0.05mm tolerance')
else:
    tap_direction = 'FRONT' if y_deviation > 0 else 'BACK'
    await self._log(f'Tap tailstock toward {tap_direction} by {abs(y_deviation):.3f}mm')

await self._log('=== TAILSTOCK CHECK COMPLETE ===')
