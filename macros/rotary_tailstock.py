# Rotary Tailstock Square Check
# Probes Y on tailstock to verify it's square with chuck centerline
# Reports Y deviation from centerline (Y=0) - does NOT set coordinates
# Run Chuck Find first to establish centerline reference
# Inputs: tool_diameter from self.tool_diameter

import asyncio

r = self.tool_diameter / 2

# Tailstock geometry:
# - 21.2/2 = 10.6mm from centerline to reference point
# - 7mm from reference point to probe edge
# - Probe edge at Y = -(7 + 10.6) = -17.6mm from centerline
# - Tool center at probe: Y = -(r + 7 + 10.6)
TAILSTOCK_EDGE_OFFSET = 7 + 21.2/2  # 17.6mm from centerline to probe edge

# Alignment tolerance (mm)
ALIGNMENT_TOLERANCE = 0.05

await self._log('=== TAILSTOCK SQUARE CHECK START ===')
await self._log(f'Tool diameter: {self.tool_diameter}mm')

# ===== Y PROBE (BOTTOM EDGE - toward chuck) =====
await self._send_and_log('G91')

# Clear probe surface (+Y to get past bottom edge)
y_clear = 6 + r
await self._send_and_log(f'G0 Y{y_clear}')
await self._wait_idle()

# Plunge with safety check
await self._send_and_log('G38.3 Z-6 F100')
await self._wait_idle()
if self.grbl.last_probe['success']:
    await self._send_and_log('G0 Z6')
    await self._send_and_log(f'G0 Y{-y_clear}')
    await self._send_and_log('G90')
    await self._log('ERROR: Unexpected probe contact during plunge')
    return

# First probe (fast) toward front (-Y)
await self._send_and_log('G38.3 Y-6 F50')
await self._wait_idle()
if not self.grbl.last_probe['success']:
    await self._send_and_log('G0 Z6')
    await self._send_and_log(f'G0 Y{-y_clear}')
    await self._send_and_log('G90')
    await self._log('ERROR: No Y probe contact')
    return

# Back off and refine
await self._send_and_log('G0 Y1')
await self._send_and_log('G38.3 Y-2 F10')
await self._wait_idle()

# Read probed Y position (work coordinates)
probed_y = self.grbl.status.wpos['y']
# Expected Y: -(r + edge_offset) when tailstock centerline is at Y=0 (aligned with chuck)
expected_y = -(r + TAILSTOCK_EDGE_OFFSET)
y_deviation = probed_y - expected_y

# Retract
await self._send_and_log('G0 Z6')
await self._send_and_log(f'G0 Y{-y_clear}')
await self._send_and_log('G90')
await self._wait_idle()

# Report
await self._log(f'Probed Y: {probed_y:.3f}mm (expected {expected_y:.3f}mm)')
await self._log(f'Deviation: {y_deviation:.3f}mm')

if abs(y_deviation) <= ALIGNMENT_TOLERANCE:
    await self._log('SQUARE: Within 0.05mm tolerance')
else:
    # Tell user which way to tap
    tap_direction = 'FRONT' if y_deviation > 0 else 'BACK'
    await self._log(f'Tap tailstock toward {tap_direction} by {abs(y_deviation):.3f}mm')

await self._log('=== TAILSTOCK SQUARE CHECK COMPLETE ===')
