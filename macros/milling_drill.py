# Peck Drill Cycle
# Inputs: depth (from self.depth), tool_dia (from self.tool_diameter)
# Peck depth = tool_dia / 2
# Full retract every 3 pecks for chip clearing

import asyncio

peck = self.tool_diameter / 2

# Get current Z position as start position (surface)
await self._wait_idle()
start_z = self.grbl.status.wpos['z']

await self._log(f'=== DRILL START: depth={self.depth}mm, peck={peck:.2f}mm ===')

await self._send_and_log('G91')
await self._send_and_log('G0 Z2')  # Retract 2mm from surface
await self._send_and_log('G90')    # Switch to absolute for drilling

await self._send_and_log('M3 S12000')
await asyncio.sleep(10)

current_depth = 0
peck_count = 0

while current_depth < self.depth:
    next_depth = min(current_depth + peck, self.depth)
    peck_count += 1

    # Rapid to clearance (0.5mm above previous cut depth)
    clearance_z = start_z - current_depth + 0.5
    await self._send_and_log(f'G0 Z{clearance_z}')

    # Feed to new depth
    target_z = start_z - next_depth
    await self._send_and_log(f'G1 Z{target_z} F300')
    await self._wait_idle()

    current_depth = next_depth

    # Retract pattern
    if peck_count % 3 == 0:
        # Full retract every 3 pecks (chip clearing)
        await self._send_and_log(f'G0 Z{start_z + 2}')
        await self._log(f'Full retract at peck {peck_count}')
    else:
        # Slight back-off (2mm)
        await self._send_and_log('G91')
        await self._send_and_log('G0 Z2')
        await self._send_and_log('G90')

# Final retract
await self._send_and_log(f'G0 Z{start_z + 2}')

await self._send_and_log('M5')
await self._log('=== DRILL COMPLETE ===')
