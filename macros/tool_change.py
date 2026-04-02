# Tool Change
# Saves position, goes to change position, waits for user,
# probes at G28, applies offset, returns to start XY at safe Z

if not self.set_z_done or self.probe_work_z is None:
    await self._log('WARNING: Measure (SetZ) not done. Press CONTINUE to measure now, or SKIP to run without tool offset.')
    await self._wait_for_continue()
    if self.skip_flag:
        self.skip_flag = False
        await self._log('=== TOOL CHANGE SKIPPED (no measure) ===')
        return

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

# Go to tool change position
await self._send_and_log('G53 G0 X-2 Y-418')
await self._wait_idle()

# Wait for user to change tool
await self._log('Swap tool, then press CONTINUE (or SKIP to skip)')
await self._wait_for_continue()

if self.skip_flag:
    self.skip_flag = False
    await self._log('=== TOOL CHANGE SKIPPED ===')
    # Return to start XY at safe Z without probing
    await self._send_and_log(f'G0 X{start_x:.3f} Y{start_y:.3f}')
    await self._wait_idle()
else:
    await self._log('=== CONTINUING ===')

    # Save old probe baseline, then probe
    old_probe_z = self.probe_work_z
    await self._probe_at_g28()

    # Calculate and apply tool offset
    tool_offset = old_probe_z - self.probe_work_z
    await self._log(f'toolOffset: {tool_offset:.3f} (old={old_probe_z:.3f} new={self.probe_work_z:.3f})')
    restore_z = start_z + offset_to_safe + tool_offset
    await self._send_and_log(f'G10 L20 P1 Z{restore_z:.3f}')

    # Return to start XY, stay at safe Z
    await self._send_and_log(f'G0 X{start_x:.3f} Y{start_y:.3f}')
    await self._wait_idle()

await self._log('=== TOOL CHANGE COMPLETE ===')
