# Peck Drill Cycle
# Inputs: depth (from self.depth), tool_dia (from self.tool_diameter)
# Peck depth = tool_dia / 2
# Pattern: 3 pecks straight down, full retract, repeat

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(macro_dir))
from config import SPINDLE_RPM, SPINDLE_WARMUP, feed_for_tool

peck = self.tool_diameter / 2
feed = getattr(self, 'feed_override', None) or feed_for_tool(self.tool_diameter)

await self._wait_idle()
start_z = self.grbl.status.wpos['z']

await self._log(f'=== DRILL START: depth={self.depth}mm, peck={peck:.2f}mm ===')
await self._send_and_log('G90')
await self._send_and_log(f'M3 S{SPINDLE_RPM}')
await asyncio.sleep(SPINDLE_WARMUP)

current_depth = 0
peck_count = 0
clearance_z = start_z  # First group starts from start_z

while current_depth < self.depth:
    # Rapid to clearance
    await self._send_and_log(f'G0 Z{clearance_z:.3f}')

    # Take up to 3 pecks
    for _ in range(3):
        if current_depth >= self.depth:
            break
        next_depth = min(current_depth + peck, self.depth)
        target_z = start_z - next_depth
        await self._send_and_log(f'G1 Z{target_z:.3f} F{feed:.0f}')
        await self._wait_idle()
        current_depth = next_depth
        peck_count += 1

    # Full retract for chip clearing
    await self._send_and_log(f'G0 Z{start_z:.3f}')
    await self._log(f'Retract after peck {peck_count}, depth={current_depth:.2f}mm')

    # Next clearance is last depth + 0.5mm
    clearance_z = start_z - current_depth + 0.5

# Already at start_z from final retract
await self._send_and_log('M5')
await self._log('=== DRILL COMPLETE ===')
