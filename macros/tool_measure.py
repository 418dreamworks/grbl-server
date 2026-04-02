# Tool Measure (SetZ)
# Records start, probes at G28, returns to start XY at safe Z

await self._wait_idle()
await self._log('=== TOOL MEASURE START ===')

# Save start position (work coords)
start_x = self.grbl.status.wpos['x']
start_y = self.grbl.status.wpos['y']
start_z = self.grbl.status.wpos['z']
await self._log(f'start: X{start_x:.3f} Y{start_y:.3f} Z{start_z:.3f}')

# Probe at G28 (raises to safe Z, goes to G28, probes, returns to safe Z)
await self._probe_at_g28()

# Return to start XY, stay at safe Z
await self._send_and_log(f'G0 X{start_x:.3f} Y{start_y:.3f}')
await self._wait_idle()

await self._log('=== TOOL MEASURE COMPLETE ===')
