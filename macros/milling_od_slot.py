# OD Slot (Single Pass Circle)
# Cut around outside of circle from current center
# Edit parameters below before running

diameter = 20  # OD diameter (mm)
depth = 5      # Cut depth (mm)

radius = diameter / 2

await self._log(f'=== OD SLOT START: D={diameter}mm depth={depth}mm ===')

# Relative mode
await self._send_and_log('G91')

# Move to edge
await self._send_and_log(f'G0 X{radius:.3f}')
await self._wait_idle()

# Plunge
await self._send_and_log(f'G1 Z-{depth:.3f} F500')
await self._wait_idle()

# CCW full circle (climb milling)
await self._send_and_log(f'G3 I-{radius:.3f} J0 F300')
await self._wait_idle()

# Retract
await self._send_and_log(f'G0 Z{depth:.3f}')
await self._wait_idle()

# Return to center
await self._send_and_log(f'G0 X-{radius:.3f}')
await self._wait_idle()

# Back to absolute
await self._send_and_log('G90')

await self._log('=== OD SLOT COMPLETE ===')
