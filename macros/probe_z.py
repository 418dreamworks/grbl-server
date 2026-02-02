# Z Zero Probe
# Tracks displacement to return to start position after zeroing

await self._wait_idle()
await self._log('=== Z PROBE START ===')

# Record start Z for displacement tracking
start_z = self.grbl.status.wpos['z']

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

# Calculate displacement from start to probe contact
probe_z = self.grbl.status.wpos['z']
displacement = start_z - probe_z  # Positive = we moved down

# Set Z to plate thickness (22mm) - still in relative mode, but G10 is always absolute
await self._send_and_log('G10 L20 P1 Z22.000')

# Return to start: in new coord system, start is at Z = 22 + displacement
# Check MPos to ensure we don't exceed safe limit
mpos_z = self.grbl.status.mpos['z']
max_safe_raise = abs(mpos_z) - 1  # Stay at MPos Z = -1 minimum
actual_raise = min(displacement, max(0, max_safe_raise))

if actual_raise > 0:
    await self._send_and_log(f'G0 Z{actual_raise:.3f}')
    await self._log(f'Returned to start (raised {actual_raise:.1f}mm)')
else:
    await self._log(f'No raise - too close to home (MPos Z = {mpos_z:.1f})')

# Back to absolute mode
await self._send_and_log('G90')

await self._log('Z set to 22mm (plate thickness)')
await self._log('=== Z PROBE COMPLETE ===')
