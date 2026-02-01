# Boring Cycle with Helical Plunge and Spiral Outward
# Inputs: bore_dia (from self.bore_dia), depth (from self.depth)
# tool_dia from self.tool_diameter
# DOC, Helical pitch, Stepover all from config ratios

import asyncio
import sys
sys.path.insert(0, str(__file__).rsplit('/', 2)[0]) if '/' in str(__file__) else None
from config import DOC_RATIO, PITCH_RATIO, STEPOVER_RATIO, HELIX_START_RADIUS, FINISH_STOCK, FEED_PLUNGE, FEED_CUT, SPINDLE_RPM, SPINDLE_WARMUP

doc = self.tool_diameter * DOC_RATIO
pitch = self.tool_diameter * PITCH_RATIO
stepover = self.tool_diameter * STEPOVER_RATIO
helix_radius = HELIX_START_RADIUS
finish_stock = FINISH_STOCK
bore_radius = self.bore_dia / 2

await self._log(f'=== BORE START: dia={self.bore_dia}mm, depth={self.depth}mm ===')

# Validation
if self.bore_dia < self.tool_diameter + 0.1:
    await self._log('ERROR: Bore diameter too small. Use smaller bit or Drill macro.')
    return

await self._send_and_log('G91')
await self._send_and_log('G0 Z2')  # Safety retract
await self._send_and_log(f'M3 S{SPINDLE_RPM}')
await asyncio.sleep(SPINDLE_WARMUP)

current_depth = 0

while current_depth < self.depth:
    level_depth = min(doc, self.depth - current_depth)

    # Move to helix start position (+X from center)
    await self._send_and_log(f'G0 X{helix_radius}')

    # Helical plunge at helix_radius
    plunge_remaining = level_depth
    while plunge_remaining > 0:
        descend = min(pitch, plunge_remaining)
        await self._send_and_log(f'G3 I{-helix_radius} J0 Z{-descend} F{FEED_PLUNGE}')
        plunge_remaining -= descend
    await self._wait_idle()

    current_depth += level_depth

    # Determine target radius for this level
    if current_depth >= self.depth:
        target = bore_radius  # Final pass - full diameter
    else:
        target = bore_radius - finish_stock  # Leave finish stock

    # Spiral outward
    current_radius = helix_radius
    while current_radius < target:
        step = min(stepover, target - current_radius)
        await self._send_and_log(f'G1 X{step} F{FEED_CUT}')
        current_radius += step
        await self._send_and_log(f'G3 I{-current_radius} J0 F{FEED_CUT}')
        await self._wait_idle()

    await self._log(f'Level {current_depth:.2f}mm complete, radius={current_radius:.3f}mm')

    # Return to center for next level
    await self._send_and_log(f'G0 X{-current_radius}')

# At center, at full depth - retract
await self._send_and_log(f'G0 Z{self.depth + 2}')

await self._send_and_log('M5')
await self._send_and_log('G90')
await self._log('=== BORE COMPLETE ===')
