/**
 * 只读拉取的自愈循环（2026-10-12）。
 *
 * 为什么单独一个文件：household.ts 里刚修好的那套「失败退避重读、读到即停、
 * 失败不清空已有数据」是写在 useEffect 里的——它 import 了 react 与 api/client.ts
 * （后者读 `import.meta.env`），在 node 里加载不起来，于是最该被钉住的循环语义
 * 恰好测不到。把循环本体搬成纯函数、计时器可注入，就能用仓库既有的 `node --test`
 * 直接跑（见 readLoop.test.ts），而 hook 里只剩「谁来读、读到写哪」。
 *
 * 三条口径（与 household.ts 同源，改这里前先读那段实测）：
 *   1 失败按 retry.ts 的阶梯排下一次重读，读到即停——**不另写一套退避**；
 *   2 数据只可能从「读到」这条路进来（onData），失败只走 onError。
 *      这就是「成功后失败绝不清空已渲染数据」的机制保证：清空在这里根本不存在；
 *   3 卸载/取消即清 timer，取消之后迟到的成功与失败都不再回调。
 */

import { readRetryDelayMs } from './retry.ts'

/** 定时器句柄：浏览器是 number、node 是对象。本模块不解释它，只原样交回 clearTimer。 */
export type TimerHandle = ReturnType<typeof setTimeout>

export interface RetryReadSpec<T> {
  /** 读一次（只读 GET）。抛错 = 这一次没读到。 */
  read: () => Promise<T>
  /** 读到了：真值原样交回。**唯一**允许写数据的地方。 */
  onData: (value: T) => void
  /** 这一次没读到。这里只该记原因，**不许**在这里清掉已经渲染出来的数据。 */
  onError: (message: string) => void
  /** 退避阶梯，缺省 retry.ts 的 readRetryDelayMs（1s→2s→…→30s 封顶）。 */
  delayMs?: (attempt: number) => number
  /** 调度器。产品代码用浏览器 setTimeout；测试注入假计时器。 */
  setTimer?: (fn: () => void, ms: number) => TimerHandle
  /** 撤销调度（与 setTimer 配套）。 */
  clearTimer?: (handle: TimerHandle) => void
}

/** 失败原因取成人能读的一句话。非 Error 的抛出（字符串、对象）也照实说，不吞。 */
export function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

/**
 * 起一次自愈读取，返回「取消」函数（组件卸载时调用，它会清掉待发的 timer）。
 *
 * 立即读第一次；失败则按阶梯排下一次（读到了就归零，不再排）。
 */
export function startRetryRead<T>(spec: RetryReadSpec<T>): () => void {
  const delayMs = spec.delayMs ?? readRetryDelayMs
  const setTimer = spec.setTimer ?? ((fn: () => void, ms: number) => setTimeout(fn, ms))
  const clearTimer =
    spec.clearTimer ??
    ((handle: TimerHandle) => {
      clearTimeout(handle)
    })

  let alive = true
  let inflight = false
  let timer: TimerHandle | null = null
  let attempt = 0

  const tick = (): void => {
    // inflight 是防御：同一时刻只允许一次在途读取，重复调度不会打成一串请求。
    if (!alive || inflight) return
    inflight = true
    spec.read()
      .then((value) => {
        if (!alive) return
        attempt = 0
        spec.onData(value)
      })
      .catch((err: unknown) => {
        if (!alive) return
        spec.onError(errorText(err))
        timer = setTimer(tick, delayMs(attempt++))
      })
      .finally(() => {
        inflight = false
      })
  }

  tick()

  return () => {
    alive = false
    if (timer !== null) clearTimer(timer)
    timer = null
  }
}

/**
 * 把「哪一块读不到、为什么」拼成一句能放到页面上的话；一处失败都没有就给 null。
 *
 * 与 household.readError 同一个理由：**读失败**与**本来就没有**在页面上必须分开说。
 * 只写「暂无数据」的话，用户分不清该等（正在自动重读）还是该去建档；
 * 而原因本身（HTTP 502 / Failed to fetch）是排查者唯一能看到的东西，不能吞掉。
 */
export function readErrorLine(
  parts: ReadonlyArray<readonly [string, string | null]>,
): string | null {
  const failed = parts.filter(([, message]) => message !== null && message !== undefined)
  if (failed.length === 0) return null
  return failed
    .map(([label, message]) => {
      const reason = String(message).trim()
      // 空原因也不静默：说出来处，并明说原因没给出来。
      return reason === '' ? `${label}读不到（未给出原因）` : `${label}读不到：${reason}`
    })
    .join('；')
}
