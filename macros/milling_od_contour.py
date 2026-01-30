# OD Contour (Multi-Pass Circle)
# Cut around outside with multiple step-down passes
# Edit parameters below before running

diameter = 20    # OD diameter (mm)
depth = 5        # Total depth (mm)
stepdown = 2     # Step-down per pass (mm)

radius = diameter / 2

await self._log(f'=== OD CONTOUR START: D={diameter}mm depth={depth}mm step={stepdown}mm ===')

# Relative mode
await self._send_and_log('G91')

# Move to edge
await self._send_and_log(f'G0 X{radius:.3f}')
await self._wait_idle()

current_depth = 0
while current_depth < depth:
    this_step = min(stepdown, depth - current_depth)

    # Step down
    await self._send_and_log(f'G1 Z-{this_step:.3f} F300')
    await self._wait_idle()

    # CCW full circle
    await self._send_and_log(f'G3 I-{radius:.3f} J0 F300')
    await self._wait_idle()

    current_depth += this_step

# Retract
await self._send_and_log(f'G0 Z{depth:.3f}')
await self._wait_idle()

# Return to center
await self._send_and_log(f'G0 X-{radius:.3f}')
await self._wait_idle()

# Back to absolute
await self._send_and_log('G90')

await self._log('=== OD CONTOUR COMPLETE ===')
