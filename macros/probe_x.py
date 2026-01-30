# X Zero Probe
# Tool starts LEFT of probe/stock, probes RIGHT
# Probe edge is 7mm left of work X=0

tool_diameter = self.tool_diameter  # Set by caller or default 6.35mm
tool_radius = tool_diameter / 2
safety_move = 5 + tool_diameter + 2

await self._wait_idle()
await self._log(f'=== X PROBE START (tool dia={tool_diameter:.3f}mm) ===')

# Relative mode
await self._send_and_log('G91')

# Move LEFT for safety
await self._send_and_log(f'G0 X-{safety_move:.3f}')
await self._wait_idle()

# First probe: fast, probe RIGHT
await self._send_and_log('G38.2 X20 F50')
await self._wait_idle()

# Back off 2mm LEFT
await self._send_and_log('G0 X-2')
await self._wait_idle()

# Second probe: medium
await self._send_and_log('G38.2 X5 F10')
await self._wait_idle()

# Absolute mode
await self._send_and_log('G90')

# Set X: probe edge 7mm + tool radius
x_offset = -(7 + tool_radius)
await self._send_and_log(f'G10 L20 P1 X{x_offset:.3f}')

await self._log(f'X set to {x_offset:.3f}mm')
await self._log('=== X PROBE COMPLETE ===')
