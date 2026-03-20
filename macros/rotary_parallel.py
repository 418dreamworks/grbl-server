# Parallel Check
# Probes Y at two X positions (50mm apart) to check if surface is parallel to X axis
# Start position: same as chuck find probe position

r = self.tool_diameter / 2

await self._wait_idle()
await self._log('=== PARALLEL CHECK ===')

# Record start position
start_x = self.grbl.status.wpos['x']
start_y = self.grbl.status.wpos['y']
start_z = self.grbl.status.wpos['z']
await self._log(f'Start: X{start_x:.3f} Y{start_y:.3f} Z{start_z:.3f}')

await self._send_and_log('G91')

# Move south to clear surface, then drop Z to probe height
await self._send_and_log(f'G0 Y{-(7 + r):.3f}')
await self._wait_idle()
await self._send_and_log('G0 Z-6')
await self._wait_idle()

# --- First Y probe (north, +Y) ---
await self._send_and_log(f'G38.3 Y{7 + r + 5:.3f} F50')
await self._wait_idle()
await self._send_and_log('G0 Y-2')
await self._wait_idle()
await self._send_and_log('G38.3 Y3 F10')
await self._wait_idle()
y1 = self.grbl.status.wpos['y']
my1 = self.grbl.status.mpos['y']
await self._log(f'Y1 = {y1:.3f} (right) MPos Y={my1:.3f}')

# Back off to clear surface
await self._send_and_log('G0 Y-2')
await self._wait_idle()

# Move left 55mm (no Z change)
await self._send_and_log('G0 X-50')
await self._wait_idle()

# --- Second Y probe (north, +Y) ---
await self._send_and_log(f'G38.3 Y{7 + r + 5:.3f} F50')
await self._wait_idle()
await self._send_and_log('G0 Y-2')
await self._wait_idle()
await self._send_and_log('G38.3 Y3 F10')
await self._wait_idle()
y2 = self.grbl.status.wpos['y']
my2 = self.grbl.status.mpos['y']
await self._log(f'Y2 = {y2:.3f} (left) MPos Y={my2:.3f}')

# Back off south
await self._send_and_log('G0 Y-2')
await self._wait_idle()

# Clear: raise Z to start height first, then return to start XY
await self._send_and_log('G90')
await self._send_and_log(f'G0 Z{start_z:.3f}')
await self._wait_idle()
await self._send_and_log(f'G0 X{start_x:.3f} Y{start_y:.3f}')
await self._wait_idle()

# Report result via continue dialog
deviation = my2 - my1
if abs(deviation) <= 0.05:
    result = f'PARALLEL: Within 0.05mm (dev={deviation:.3f}mm over 50mm)\nLeft Y={my2:.3f}  |  Right Y={my1:.3f}'
else:
    direction = 'LEFT end toward Y-' if deviation > 0 else 'LEFT end toward Y+'
    result = f'Tap {direction} by {abs(deviation):.3f}mm (over 50mm)\nLeft Y={my2:.3f}  |  Right Y={my1:.3f}'

await self._log(result)
# Show result in continue dialog
self.continue_event.clear()
if self.broadcast_callback:
    await self.broadcast_callback({
        'type': 'macro_status',
        'name': self.current_macro,
        'step': self.current_step,
        'description': result,
        'waiting': True,
    })
await self.continue_event.wait()

await self._log('=== PARALLEL CHECK COMPLETE ===')
