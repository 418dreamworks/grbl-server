# Chuck Y - Set Y=0 at rotary center
# Calls probe_y (BACK edge) which sets Y=0 at jaw edge
# Then sets Y = -REF_EDGE_TO_CENTER so center = Y0

REF_EDGE_TO_CENTER = 23.200

await self._log(f'=== CHUCK Y (offset={REF_EDGE_TO_CENTER}) ===')
await self._wait_idle()

# Run probe_y BACK — sets Y=0 at jaw edge
await self._exec_macro('probe_y', tool_diameter=self.tool_diameter, edge_sign=-1)
await self._wait_idle()

# Shift: center is REF_EDGE_TO_CENTER above jaw edge
current_y = self.grbl.status.wpos['y']
new_y = current_y - REF_EDGE_TO_CENTER
await self._send_and_log(f'G10 L20 P1 Y{new_y:.3f}')
await self._log(f'Y shifted by {-REF_EDGE_TO_CENTER:.3f}mm (center = Y0)')

await self._log('=== CHUCK Y COMPLETE ===')
