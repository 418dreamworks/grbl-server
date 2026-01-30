# Line Slot (Multi-Pass)
# Cut a slot with multiple step-down passes
# Edit parameters below before running

length = 20      # Slot length (mm)
depth = 5        # Total depth (mm)
stepdown = 2     # Step-down per pass (mm)
direction = 'X'  # Direction: 'X' or 'Y'

await self._log(f'=== LINE SLOT START: L={length}mm depth={depth}mm step={stepdown}mm dir={direction} ===')

# Relative mode
await self._send_and_log('G91')

current_depth = 0
while current_depth < depth:
    this_step = min(stepdown, depth - current_depth)

    # Step down
    await self._send_and_log(f'G1 Z-{this_step:.3f} F300')
    await self._wait_idle()

    # Cut back and forth
    if direction.upper() == 'X':
        await self._send_and_log(f'G1 X{length:.3f} F400')
        await self._wait_idle()
        await self._send_and_log(f'G1 X-{length:.3f} F400')
        await self._wait_idle()
    else:
        await self._send_and_log(f'G1 Y{length:.3f} F400')
        await self._wait_idle()
        await self._send_and_log(f'G1 Y-{length:.3f} F400')
        await self._wait_idle()

    current_depth += this_step

# Retract
await self._send_and_log(f'G0 Z{depth:.3f}')
await self._wait_idle()

# Back to absolute
await self._send_and_log('G90')

await self._log('=== LINE SLOT COMPLETE ===')
