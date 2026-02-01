# Rectangle Facing with Ramp Entry
# Inputs: length (from self.length), width (from self.width), depth (from self.depth)
# tool_dia from self.tool_diameter
# DOC = tool_dia * DOC_RATIO, Stepover = tool_dia * STEPOVER_RATIO
# Ramp entry avoids slotting (never full radial engagement)

import asyncio
import sys
sys.path.insert(0, str(__file__).rsplit('/', 2)[0]) if '/' in str(__file__) else None
from config import DOC_RATIO, STEPOVER_RATIO, FEED_CUT, SPINDLE_RPM, SPINDLE_WARMUP

doc = self.tool_diameter * DOC_RATIO
stepover = self.tool_diameter * STEPOVER_RATIO

await self._log(f'=== FACING START: {self.length}x{self.width}mm, depth={self.depth}mm ===')

await self._send_and_log('G91')
await self._send_and_log(f'M3 S{SPINDLE_RPM}')
await asyncio.sleep(SPINDLE_WARMUP)

current_depth = 0

while current_depth < self.depth:
    level_depth = min(self.depth - current_depth, doc)
    ramp_per_pass = level_depth / 2

    # === Ramp entry (3 passes) ===
    # Pass 1: 0→length, descend half
    await self._send_and_log(f'G1 X{self.length} Z{-ramp_per_pass} F{FEED_CUT}')

    # Pass 2: length→0, descend half
    await self._send_and_log(f'G1 X{-self.length} Z{-ramp_per_pass} F{FEED_CUT}')

    # Pass 3: 0→length, flat (cleanup)
    await self._send_and_log(f'G1 X{self.length} F{FEED_CUT}')
    await self._wait_idle()

    # Now at (length, 0) at new depth

    # === Zigzag ===
    y_pos = 0
    at_right = True

    while y_pos < self.width:
        # Step in Y
        step = min(stepover, self.width - y_pos)
        await self._send_and_log(f'G1 Y{step} F{FEED_CUT}')
        y_pos += step

        # Cut in X
        if at_right:
            await self._send_and_log(f'G1 X{-self.length} F{FEED_CUT}')
            at_right = False
        else:
            await self._send_and_log(f'G1 X{self.length} F{FEED_CUT}')
            at_right = True
        await self._wait_idle()

    current_depth += level_depth
    await self._log(f'Level {current_depth:.2f}mm complete')

    # Return to (0, 0) for next depth level
    if at_right:
        await self._send_and_log(f'G0 X{-self.length}')
    await self._send_and_log(f'G0 Y{-self.width}')

# Retract and end
await self._send_and_log(f'G0 Z{self.depth + 2}')
await self._send_and_log('M5')
await self._send_and_log('G90')
await self._log('=== FACING COMPLETE ===')
