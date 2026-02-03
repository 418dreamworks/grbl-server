# Z Zero Probe
await self._wait_idle()
await self._log('=== Z PROBE START ===')

# Switch to relative mode
await self._send_and_log('G91')

# First probe: fast (F50), max 11mm down
await self._send_and_log('G38.2 Z-11 F50')
await self._wait_idle()

# Back off 2.5mm
await self._send_and_log('G0 Z2.5')
await self._wait_idle()

# Second probe: medium (F10), max 3mm down
await self._send_and_log('G38.2 Z-3 F10')
await self._wait_idle()

# Set Z to plate thickness (22mm) - still in relative mode, but G10 is always absolute
await self._send_and_log('G10 L20 P1 Z22.000')

# Safe raise: check MPos Z and don't go past -1
mpos_z = self.grbl.status.mpos['z']
max_safe_raise = abs(mpos_z) - 1  # Stay at MPos Z = -1 minimum
desired_raise = 10
actual_raise = min(desired_raise, max(0, max_safe_raise))

if actual_raise > 0:
    await self._send_and_log(f'G0 Z{actual_raise:.1f}')
    await self._log(f'Raised {actual_raise:.1f}mm (MPos Z was {mpos_z:.1f})')
else:
    await self._log(f'No raise - too close to home (MPos Z = {mpos_z:.1f})')

# Back to absolute mode
await self._send_and_log('G90')

await self._log('Z set to 22mm (plate thickness)')
await self._log('=== Z PROBE COMPLETE ===')
