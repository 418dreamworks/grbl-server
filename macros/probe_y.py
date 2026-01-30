# Y Zero Probe
# Tool starts BELOW (front of) probe/stock, probes FORWARD (+Y)
# Probe edge is 7mm below work Y=0

tool_diameter = self.tool_diameter  # Set by caller or default 6.35mm
tool_radius = tool_diameter / 2
safety_move = 5 + tool_diameter + 2

await self._wait_idle()
await self._log(f'=== Y PROBE START (tool dia={tool_diameter:.3f}mm) ===')

# Relative mode
await self._send_and_log('G91')

# Move BACKWARD for safety
await self._send_and_log(f'G0 Y-{safety_move:.3f}')
await self._wait_idle()

# First probe: fast, probe FORWARD
await self._send_and_log('G38.2 Y20 F50')
await self._wait_idle()

# Back off 2mm
await self._send_and_log('G0 Y-2')
await self._wait_idle()

# Second probe: medium
await self._send_and_log('G38.2 Y5 F10')
await self._wait_idle()

# Absolute mode
await self._send_and_log('G90')

# Set Y: probe edge 7mm + tool radius
y_offset = -(7 + tool_radius)
await self._send_and_log(f'G10 L20 P1 Y{y_offset:.3f}')

await self._log(f'Y set to {y_offset:.3f}mm')
await self._log('=== Y PROBE COMPLETE ===')
