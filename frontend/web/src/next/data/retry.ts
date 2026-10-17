/**
 * 只读拉取失败后的重读阶梯（毫秒）。放成纯模块，是为了能被 `node --test` 直接钉住
 * ——它的宿主 household.ts 头部 import 了 react 与 api/client.ts（读 import.meta.env），
 * 在 node 里加载不起来（同 fieldFacts.ts 头部写下的那条理由）。
 *
 * 为什么需要重读（2026-10-12 实测的真实故障，不是预防性重构）：
 *   这些读取发生在**页面加载那一刻**，而那一刻未必是后端可用的一刻。
 *   用户重启后端（新代码生效）期间刷新 /next.html，实测 load 期 22 个 /api/* 请求
 *   **全部 502**（performance resource：responseStatus=502、decodedBodySize=0），
 *   而读取链路里只有 `.catch(() => {})`：失败被吞成"这份数据不存在"，
 *   重读又只挂在 member-switched 广播上。于是后端恢复后页面一直停在兜底文案，
 *   用户看不出是"网络失败"还是"真的没有档案"，只能靠再次刷新。
 *
 * 阶梯的口径（有上限、不静默放弃、也不打爆后端）：
 *   1s → 2s → 4s → 8s → 16s → 30s → 30s …（只在该次读取失败时排下一次，读到即停）。
 *   上限 30s 是有意的：一个开着不动的页面每 30s 一次只读 GET 可以忽略，
 *   而比"永远停在兜底文案"诚实得多——这两种代价不对等。
 */

/** 退避上限。至少要能被后端一次重启（实测窗口约 2 分钟、含模型加载）覆盖过去。 */
export const READ_RETRY_MAX_MS = 30_000

/** 第 attempt 次失败之后等多久再读（attempt 从 0 起）。非法/负数一律按第 0 次算。 */
export function readRetryDelayMs(attempt: number): number {
  const n = Number.isFinite(attempt) && attempt > 0 ? Math.floor(attempt) : 0
  // 2**n 在 n 很大时会变成 Infinity，Math.min 会把它收回上限，不需要额外分支。
  return Math.min(1000 * 2 ** n, READ_RETRY_MAX_MS)
}
