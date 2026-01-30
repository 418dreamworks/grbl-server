# Tool Change
# Changes tool and re-probes to calculate Z offset
# Requires Tool Measure to be run first

# Check if SetZ was done
if not self.set_z_done or self.probe_work_z is None:
    await self._log('ERROR: Run Tool Measure first!')
    raise Exception('SetZ must be run first')

await self._wait_idle()
await self._log('=== TOOL CHANGE START ===')

# Save start position (work coords)
start_x = self.grbl.status.wpos['x']
start_y = self.grbl.status.wpos['y']
start_z = self.grbl.status.wpos['z']
await self._log(f'start: X{start_x:.3f} Y{start_y:.3f} Z{start_z:.3f}')

# Raise to safe Z
await self._send_and_log('G53 G0 Z-1')
await self._wait_idle()

# Calculate offset to safe
offset_to_safe = self.grbl.status.wpos['z'] - start_z
await self._log(f'offsetToSafe: {offset_to_safe:.3f}')

# Go to tool change position (machine coords)
await self._send_and_log('G53 G0 X-2 Y-418')
await self._wait_idle()

# Wait for user to change tool
await self._log('=== WAITING FOR TOOL CHANGE ===')
await self._wait_for_continue()
await self._log('=== CONTINUING ===')

# Zero work Z temporarily
await self._send_and_log('G10 L20 P1 Z0')

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

# Calculate tool offset
new_mposz = self.grbl.status.mpos['z']
tool_offset = self.probe_work_z - new_mposz
await self._log(f'toolOffset: {tool_offset:.3f} (probeWorkZ={self.probe_work_z:.3f} - mposz={new_mposz:.3f})')

# Update probeWorkZ for next tool change
self.probe_work_z = new_mposz
await self._log(f'probeWorkZ updated to {self.probe_work_z:.3f}')

# Return to safe Z
await self._send_and_log('G53 G0 Z-1')
await self._wait_idle()

# Apply offset to work Z
restore_z = start_z + offset_to_safe + tool_offset
await self._send_and_log(f'G10 L20 P1 Z{restore_z:.3f}')

# Return to start XY then Z
await self._send_and_log(f'G0 X{start_x:.3f} Y{start_y:.3f}')
await self._wait_idle()
await self._send_and_log(f'G0 Z{start_z:.3f}')
await self._wait_idle()

await self._log('=== TOOL CHANGE COMPLETE ===')
