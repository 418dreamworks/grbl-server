# Tool Measure (SetZ)
# Measures first tool's Z position at probe location
# Must run before Tool Change

await self._wait_idle()
await self._log('=== TOOL MEASURE START ===')

# Save start position (work coords)
start_x = self.grbl.status.wpos['x']
start_y = self.grbl.status.wpos['y']
start_z = self.grbl.status.wpos['z']
await self._log(f'start: X{start_x:.3f} Y{start_y:.3f} Z{start_z:.3f}')

# Raise to safe Z
await self._send_and_log('G53 G0 Z-1')
await self._wait_idle()

# Calculate offset
offset = self.grbl.status.wpos['z'] - start_z
await self._log(f'offset: {offset:.3f}')

# Temp set work Z
await self._send_and_log('G10 L20 P1 Z-1')

# Go to G28 probe position
g28 = self.grbl.g28_pos
await self._log(f'G28 pos: X{g28["x"]:.3f} Y{g28["y"]:.3f} Z{g28["z"]:.3f}')

if g28['x'] == 0 and g28['y'] == 0 and g28['z'] == 0:
    await self._log('WARNING: G28 position is 0,0,0 - use G28.1 to store probe position')

await self._send_and_log(f'G53 G0 X{g28["x"]:.3f} Y{g28["y"]:.3f} Z{g28["z"]:.3f}')
await self._wait_idle()

# Probe fast
await self._send_and_log('G90')
await self._send_and_log('G38.2 Z-78 F600')
await self._wait_idle()

# Back off and probe slow
await self._send_and_log('G91')
await self._send_and_log('G0 Z2')
await self._wait_idle()
await self._send_and_log('G38.2 Z-4 F10')
await self._wait_idle()
await self._send_and_log('G90')

# Store probe Z (machine coords) for tool change reference
self.probe_work_z = self.grbl.status.mpos['z']
self.set_z_done = True
await self._log(f'probeWorkZ = {self.probe_work_z:.3f} (machine)')

# Return to safe Z
await self._send_and_log('G53 G0 Z-1')
await self._wait_idle()

# Restore work Z
restore_z = offset + start_z
await self._send_and_log(f'G10 L20 P1 Z{restore_z:.3f}')

# Return to start XY then Z
await self._send_and_log(f'G0 X{start_x:.3f} Y{start_y:.3f}')
await self._wait_idle()
await self._send_and_log(f'G0 Z{start_z:.3f}')
await self._wait_idle()

await self._log('=== TOOL MEASURE COMPLETE ===')
