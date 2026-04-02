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

# Back off 1mm
await self._send_and_log('G0 Z0.5')
await self._wait_idle()

# Second probe: medium (F10), max 1.5mm down
await self._send_and_log('G38.2 Z-1.5 F1')
await self._wait_idle()

# Calculate displacement from start to probe contact
probe_z = self.grbl.status.wpos['z']
displacement = start_z - probe_z  # Positive = we moved down

# Set Z to plate thickness (22mm) - still in relative mode, but G10 is always absolute
await self._send_and_log('G10 L20 P1 Z22.000')

# Return to start Z (in new coord system: 22 + displacement)
await self._send_and_log('G90')
await self._send_and_log(f'G0 Z{22.0 + displacement:.3f}')
await self._wait_idle()

await self._log('Z set to 22mm (plate thickness)')
await self._log('=== Z PROBE COMPLETE ===')
