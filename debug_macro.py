# Z Zero Probe
await self._wait_idle()
await self._log('=== Z PROBE START ===')

# Switch to relative mode
await self._send_and_log('G91')

# First probe: fast (F50), max 15mm down
await self._send_and_log('G38.2 Z-11 F50')
await self._wait_idle()

# Back off 2mm
await self._send_and_log('G0 Z2.5')
await self._wait_idle()

# Second probe: medium (F10), max 10mm down
await self._send_and_log('G38.2 Z-3 F10')
await self._wait_idle()

# Back to absolute mode
await self._send_and_log('G90')

# Set Z to plate thickness (22mm)
await self._send_and_log('G10 L20 P1 Z22.000')

await self._send_and_log('G0 Z32.000')


await self._log('Z set to 22mm (plate thickness)')
await self._log('=== Z PROBE COMPLETE ===')