# Chuck Z - Set Z=0 at rotary center
# Calls probe_z to find jaw surface (Z=0 at jaw after probe_z)
# Then shifts Z so center = 0 (jaw = JAW_TO_CENTER)

JAW_TO_CENTER = 25.250

await self._log(f'=== CHUCK Z (offset={JAW_TO_CENTER}) ===')
await self._wait_idle()

# Run probe_z to find jaw surface (sets Z=0 at jaw)
await self._exec_macro('probe_z')

# Shift Z: jaw is JAW_TO_CENTER above center
await self._wait_idle()
current_z = self.grbl.status.wpos['z']
new_z = current_z + JAW_TO_CENTER
await self._send_and_log(f'G10 L20 P1 Z{new_z:.3f}')
await self._log(f'Z shifted by {JAW_TO_CENTER:.3f}mm (center = Z0, jaw = Z{JAW_TO_CENTER:.3f})')

await self._log('=== CHUCK Z COMPLETE ===')
