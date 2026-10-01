import { test } from 'node:test'
import { strict as assert } from 'node:assert'
import { spawnSync } from 'node:child_process'
import { resolve } from 'node:path'

test('hardware probe fails closed without the Windows bridge and emits no session URL', () => {
  const root = resolve(import.meta.dirname, '..', '..')
  const result = spawnSync(process.execPath, ['ui/scripts/measure-hardware.mjs'], {
    cwd: root,
    env: { ...process.env, PATH: '/usr/bin:/bin' },
    encoding: 'utf8',
    timeout: 15000,
  })
  assert.equal(result.status, 2)
  const report = JSON.parse(result.stdout)
  assert.equal(report.gate, 'unmeasured')
  assert.deepEqual(report.samples, [])
  assert.equal(result.stdout.includes('#token='), false)
  assert.equal(result.stdout.includes('app_url'), false)
})
