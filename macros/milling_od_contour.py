# OD Contour - Circular Pocket with Helical Plunge + Spiral
# Inputs: start_dia, end_dia, depth (from self.start_dia, self.end_dia, self.depth)
# Optional: rapid_plunge (from self.rapid_plunge) - if True, single helix to full depth
# Current position = CENTER of circle
# Direction: start_dia < end_dia = spiral OUT, start_dia > end_dia = spiral IN
# When start_dia == end_dia: Just helical descent (circular slot)

import asyncio
import sys
sys.path.insert(0, str(__file__).rsplit('/', 2)[0]) if '/' in str(__file__) else None
from config import DOC_RATIO, PITCH_RATIO, STEPOVER_RATIO, FEED_PLUNGE, FEED_CUT, SPINDLE_RPM, SPINDLE_WARMUP

doc = self.tool_diameter * DOC_RATIO
pitch = self.tool_diameter * PITCH_RATIO
stepover = self.tool_diameter * STEPOVER_RATIO

# Rapid plunge option - single pass to full depth
rapid_plunge = getattr(self, 'rapid_plunge', False)

start_radius = self.start_dia / 2
end_radius = self.end_dia / 2

# Direction: +1 = spiral OUT, -1 = spiral IN, 0 = slot only
if end_radius > start_radius:
    step_sign = 1
    mode = 'OUT'
elif end_radius < start_radius:
    step_sign = -1
    mode = 'IN'
else:
    step_sign = 0
    mode = 'SLOT'

plunge_mode = 'RAPID' if rapid_plunge else 'NORMAL'
await self._log(f'=== OD CONTOUR START: {self.start_dia}mm → {self.end_dia}mm ({mode}), depth={self.depth}mm, plunge={plunge_mode} ===')

await self._send_and_log('G91')
await self._send_and_log(f'M3 S{SPINDLE_RPM}')
await asyncio.sleep(SPINDLE_WARMUP)

if rapid_plunge:
    # RAPID PLUNGE MODE: Single helix to full depth, then one spiral pass
    await self._send_and_log(f'G0 X{start_radius}')

    # Helical plunge to full depth
    plunge_remaining = self.depth
    while plunge_remaining > 0:
        descend = min(pitch, plunge_remaining)
        await self._send_and_log(f'G3 I{-start_radius} J0 Z{-descend} F{FEED_PLUNGE}')
        plunge_remaining -= descend
    await self._wait_idle()

    if step_sign != 0:
        # Single spiral to end radius
        current_radius = start_radius
        target = end_radius

        while (step_sign > 0 and current_radius < target) or \
              (step_sign < 0 and current_radius > target):
            await self._send_and_log(f'G3 I{-current_radius} J0 F{FEED_CUT}')
            remaining = abs(target - current_radius)
            step = min(stepover, remaining)
            await self._send_and_log(f'G1 X{step_sign * step} F{FEED_CUT}')
            current_radius += step_sign * step
            await self._wait_idle()

        await self._send_and_log(f'G3 I{-current_radius} J0 F{FEED_CUT}')
    else:
        current_radius = start_radius
        await self._send_and_log(f'G3 I{-current_radius} J0 F{FEED_CUT}')

    await self._log(f'Rapid plunge complete at {self.depth:.2f}mm')
    await self._send_and_log(f'G0 X{-current_radius}')

else:
    # NORMAL MODE: Multiple passes with DOC limit
    current_depth = 0

    while current_depth < self.depth:
        level_depth = min(doc, self.depth - current_depth)

        # Move to start radius (+X from center)
        await self._send_and_log(f'G0 X{start_radius}')

        # Helical plunge at start_radius (3 spirals per DOC)
        plunge_remaining = level_depth
        while plunge_remaining > 0:
            descend = min(pitch, plunge_remaining)
            await self._send_and_log(f'G3 I{-start_radius} J0 Z{-descend} F{FEED_PLUNGE}')
            plunge_remaining -= descend
        await self._wait_idle()

        current_depth += level_depth

        if step_sign != 0:
            # Spiral horizontally toward end_radius (Z fixed)
            current_radius = start_radius
            target = end_radius

            while (step_sign > 0 and current_radius < target) or \
                  (step_sign < 0 and current_radius > target):
                # Full circle at current radius
                await self._send_and_log(f'G3 I{-current_radius} J0 F{FEED_CUT}')

                # Step toward target
                remaining = abs(target - current_radius)
                step = min(stepover, remaining)
                await self._send_and_log(f'G1 X{step_sign * step} F{FEED_CUT}')
                current_radius += step_sign * step
                await self._wait_idle()

            # Cleanup circle at end radius
            await self._send_and_log(f'G3 I{-current_radius} J0 F{FEED_CUT}')
        else:
            # SLOT MODE: just one cleanup circle at same radius
            current_radius = start_radius
            await self._send_and_log(f'G3 I{-current_radius} J0 F{FEED_CUT}')

        await self._log(f'Level {current_depth:.2f}mm complete')

        # Return to center for next level
        await self._send_and_log(f'G0 X{-current_radius}')

# Retract
await self._send_and_log(f'G0 Z{self.depth + 2}')

await self._send_and_log('M5')
await self._send_and_log('G90')
await self._log('=== OD CONTOUR COMPLETE ===')
