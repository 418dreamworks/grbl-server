# Facing Cycle
# Zigzag pattern from current position
# Edit parameters below before running

width = 50     # Face width (mm)
length = 50    # Face length (mm)
stepover = 5   # Stepover between passes (mm)
depth = 2      # Cut depth (mm)

await self._log(f'=== FACING START: {width}x{length}mm step={stepover}mm depth={depth}mm ===')

# Relative mode
await self._send_and_log('G91')

# Plunge
await self._send_and_log(f'G1 Z-{depth:.3f} F500')
await self._wait_idle()

# Zigzag pattern
y = 0
direction = 1
while y < length:
    await self._send_and_log(f'G1 X{direction * width:.3f} F800')
    await self._wait_idle()
    if y + stepover < length:
        await self._send_and_log(f'G1 Y{stepover:.3f} F800')
        await self._wait_idle()
    y += stepover
    direction *= -1

# Retract
await self._send_and_log(f'G0 Z{depth:.3f}')
await self._wait_idle()

# Back to absolute
await self._send_and_log('G90')

await self._log('=== FACING COMPLETE ===')
