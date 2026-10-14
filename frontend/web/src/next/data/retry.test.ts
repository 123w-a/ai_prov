/**
 * 重读阶梯的测试。用 `node --test` 直接跑：
 *
 *     node --test src/next/data/retry.test.ts
 *
 * 钉住的是三件在真实故障里会直接决定"能不能自愈"的事：
 *   ① 有延迟、但不是死等（第一次 1s）；
 *   ② 单调不减、且有上限（不许 2**n 无限涨到几小时，那等于不重读）；
 *   ③ 累计覆盖得住后端一次重启（实测窗口约 2 分钟：19:05:57 全 502，19:07:46 后端还在加载模型）。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import { READ_RETRY_MAX_MS, readRetryDelayMs } from './retry.ts'

test('第一次失败等 1s（不是 0：0 会在后端还没起来时打成忙等）', () => {
  assert.equal(readRetryDelayMs(0), 1000)
  assert.equal(readRetryDelayMs(1), 2000)
  assert.equal(readRetryDelayMs(2), 4000)
  assert.equal(readRetryDelayMs(3), 8000)
  assert.equal(readRetryDelayMs(4), 16000)
})

test('阶梯单调不减、且有上限（不许涨到几小时，也不许归零）', () => {
  let prev = 0
  for (let attempt = 0; attempt < 40; attempt++) {
    const d = readRetryDelayMs(attempt)
    assert.ok(d >= prev, `第 ${attempt} 次退避 ${d} 比上一次 ${prev} 还短`)
    assert.ok(d <= READ_RETRY_MAX_MS, `第 ${attempt} 次退避 ${d} 超过上限 ${READ_RETRY_MAX_MS}`)
    assert.ok(d >= 1000, `第 ${attempt} 次退避 ${d} 太小`)
    prev = d
  }
  assert.equal(readRetryDelayMs(5), READ_RETRY_MAX_MS)
  assert.equal(readRetryDelayMs(999), READ_RETRY_MAX_MS, '越往后不许超过上限')
})

test('累计能覆盖后端一次重启（实测窗口约 2 分钟）', () => {
  let total = 0
  for (let attempt = 0; attempt < 8; attempt++) total += readRetryDelayMs(attempt)
  assert.ok(total >= 120_000, `8 次退避累计只有 ${total}ms，覆盖不住实测的重启窗口`)
})

test('畸形入参按第 0 次算（NaN / 负数 / 小数不许算出 NaN 延迟）', () => {
  assert.equal(readRetryDelayMs(Number.NaN), 1000)
  assert.equal(readRetryDelayMs(-1), 1000)
  assert.equal(readRetryDelayMs(Number.POSITIVE_INFINITY), 1000)
  assert.equal(readRetryDelayMs(2.7), 4000)
})
