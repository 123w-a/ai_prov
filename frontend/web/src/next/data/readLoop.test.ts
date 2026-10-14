/**
 * 自愈读取循环的测试。用 `node --test` 直接跑：
 *
 *     node --test src/next/data/readLoop.test.ts
 *
 * 钉住的是四件在真实故障（2026-10-12 后端重启窗口、load 期 22 个 /api/* 全 502）里
 * 直接决定「能不能自愈」与「会不会把真数据抹掉」的事：
 *   ① 读到即停，不再空转；
 *   ② 失败按 retry.ts 的阶梯排下一次（不是另写一套退避）；
 *   ③ 失败只走 onError，onData 一次都不被调用——已渲染的数据不可能被清空；
 *   ④ 取消后清 timer、迟到的结果不再回调（组件卸载不留悬挂回调）。
 *
 * 计时器是注入的假时钟：真等 1s→2s→4s 会让测试跑成分钟级，也会把 flaky 引进来。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import { errorText, readErrorLine, startRetryRead, type TimerHandle } from './readLoop.ts'

/** 让微任务队列跑干净（promise 的 then/catch/finally 都落定）。 */
const flush = (): Promise<void> => new Promise((resolve) => setImmediate(resolve))

/** 假计时器：手动触发，并把「排了多长」记下来供断言。 */
function clock() {
  let seq = 0
  const queue = new Map<number, { fn: () => void; ms: number }>()
  return {
    /** 当前待触发的那几次的延迟（毫秒），按排队顺序。 */
    delays: () => [...queue.values()].map((e) => e.ms),
    size: () => queue.size,
    setTimer: (fn: () => void, ms: number): TimerHandle => {
      queue.set(++seq, { fn, ms })
      return seq as unknown as TimerHandle
    },
    clearTimer: (handle: TimerHandle): void => {
      queue.delete(handle as unknown as number)
    },
    /** 触发最早排下的那一次，并等它内部那条 promise 链落定。 */
    fire: async (): Promise<void> => {
      const first = [...queue.entries()][0]
      if (!first) throw new Error('没有任何待触发的重读，却要触发它')
      queue.delete(first[0])
      first[1].fn()
      await flush()
    },
  }
}

test('读到即停：onData 收到真值，不再排下一次', async () => {
  const c = clock()
  const got: string[] = []
  const errs: string[] = []
  let calls = 0

  startRetryRead<string>({
    read: async () => {
      calls++
      return '真值'
    },
    onData: (v) => got.push(v),
    onError: (m) => errs.push(m),
    setTimer: c.setTimer,
    clearTimer: c.clearTimer,
  })
  await flush()

  assert.equal(calls, 1)
  assert.deepEqual(got, ['真值'])
  assert.deepEqual(errs, [])
  assert.equal(c.size(), 0, '读到了还排下一次，就是在空转')
})

test('失败后按 retry.ts 的阶梯重读（1s→2s→4s），读到后不再排', async () => {
  const c = clock()
  const got: string[] = []
  const errs: string[] = []
  let calls = 0

  startRetryRead<string>({
    read: async () => {
      calls++
      if (calls <= 3) throw new Error('HTTP 502')
      return '真值'
    },
    onData: (v) => got.push(v),
    onError: (m) => errs.push(m),
    setTimer: c.setTimer,
    clearTimer: c.clearTimer,
  })
  await flush()

  assert.equal(calls, 1)
  assert.deepEqual(errs, ['HTTP 502'], '失败原因要原样上报，不能吞')
  assert.deepEqual(c.delays(), [1000])

  await c.fire()
  assert.equal(calls, 2)
  assert.deepEqual(c.delays(), [2000])

  await c.fire()
  assert.equal(calls, 3)
  assert.deepEqual(c.delays(), [4000])

  await c.fire()
  assert.equal(calls, 4)
  assert.deepEqual(got, ['真值'])
  assert.equal(c.size(), 0, '读到之后必须停')
})

test('失败只走 onError：onData 一次都不被调用（已渲染的数据不可能被清空）', async () => {
  const c = clock()
  const got: unknown[] = []
  const errs: string[] = []

  startRetryRead<string>({
    read: async () => {
      throw new Error('HTTP 502')
    },
    onData: (v) => got.push(v),
    onError: (m) => errs.push(m),
    setTimer: c.setTimer,
    clearTimer: c.clearTimer,
  })
  await flush()
  await c.fire()
  await c.fire()

  assert.equal(got.length, 0, '失败期间没有任何数据写入——清空在这条链上根本不存在')
  assert.deepEqual(errs, ['HTTP 502', 'HTTP 502', 'HTTP 502'])
})

test('取消即清 timer，且迟到的成功 / 失败都不再回调', async () => {
  const c = clock()
  const got: string[] = []
  const errs: string[] = []
  let release: ((v: string) => void) | null = null

  const stop = startRetryRead<string>({
    read: () =>
      new Promise<string>((resolve) => {
        release = resolve
      }),
    onData: (v) => got.push(v),
    onError: (m) => errs.push(m),
    setTimer: c.setTimer,
    clearTimer: c.clearTimer,
  })
  await flush()
  stop()
  release?.('迟到的真值')
  await flush()

  assert.deepEqual(got, [], '卸载之后到达的结果不许再写状态')
  assert.deepEqual(errs, [])
  assert.equal(c.size(), 0)

  // 失败分支：先失败排下 timer，再取消——那个 timer 必须被清掉（不是"本来就没排"）。
  const c2 = clock()
  let reject2: ((e: Error) => void) | null = null
  const seen: string[] = []
  const stop2 = startRetryRead<string>({
    read: () =>
      new Promise<string>((_resolve, reject) => {
        reject2 = reject
      }),
    onData: (v) => seen.push(`data:${v}`),
    onError: (m) => seen.push(`err:${m}`),
    setTimer: c2.setTimer,
    clearTimer: c2.clearTimer,
  })
  await flush()
  stop2()
  reject2?.(new Error('HTTP 502'))
  await flush()

  assert.equal(c2.size(), 0, '取消后不许留下待触发的重读')
  assert.deepEqual(seen, [], '取消之后连失败原因也不许再上报（回调属于已卸载的组件）')
})

test('readErrorLine：一处失败都没有给 null；有失败就带出处与原因，空原因也不静默', () => {
  assert.equal(readErrorLine([['本周', null], ['待确认', null]]), null)
  assert.equal(readErrorLine([['本周', 'HTTP 502'], ['待确认', null]]), '本周读不到：HTTP 502')
  assert.equal(
    readErrorLine([['本周', 'HTTP 502'], ['待确认', 'Failed to fetch']]),
    '本周读不到：HTTP 502；待确认读不到：Failed to fetch',
  )
  assert.equal(readErrorLine([['本周', '   ']]), '本周读不到（未给出原因）')
})

test('errorText：非 Error 的抛出也照实说，不吞成空串', () => {
  assert.equal(errorText(new Error('HTTP 502')), 'HTTP 502')
  assert.equal(errorText('bare string'), 'bare string')
  assert.equal(errorText(undefined), 'undefined')
})
