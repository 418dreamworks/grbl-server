# Spindle Break-In
# Runs spindle from 2000 to 24000 RPM in 2000 RPM increments
# 5 minutes at each speed

import asyncio

speeds = list(range(2000, 26000, 2000))  # 2000, 4000, ... 24000
duration = 5 * 60  # 5 minutes in seconds

await self._log('=== SPINDLE BREAK-IN ===')
await self._log(f'Speeds: {speeds[0]} to {speeds[-1]} RPM')
await self._log(f'Duration: 5 min per speed, {len(speeds)} steps')
await self._log(f'Total time: {len(speeds) * 5} minutes')

for i, rpm in enumerate(speeds):
    await self._log(f'Step {i+1}/{len(speeds)}: {rpm} RPM for 5 min')
    await self._send_and_log(f'M3 S{rpm}')

    # Wait 5 minutes, logging progress each minute
    for minute in range(5):
        await asyncio.sleep(60)
        await self._log(f'  {rpm} RPM - {minute+1}/5 min complete')

    await self._log(f'  {rpm} RPM complete')

await self._send_and_log('M5')
await self._log('=== BREAK-IN COMPLETE ===')
