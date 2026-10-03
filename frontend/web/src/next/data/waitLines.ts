import type { RunState } from './model.ts'
import { formatSpoken, isStale, sinceLastHeartbeat, stagePhrase, stageSentence } from './model.ts'

/**
 * 等待页的三组纯文案推导 + 时钟换算（2026-10-01 从 WaitCard 下沉）。
 *
 * 下沉理由：WaitCard.tsx 319 行越过 blocks 层 150 行红线，且文件头那段
 * 「推翻时间卷带」的决策史、JSX 里「当前工位提升」的有理由推翻注释，
 * 与这三组**可独立检验的文案规则**混在一个文件里，任何一条规则改动都要
 * 翻过整段历史叙事。规则搬到这里、留在原处的注释原样跟随各自的主题。
 *
 * 这里的东西全部是纯函数：不发请求、不碰状态、不 import 任何 .tsx
 * （data 层不许进组件，见 scripts/check-layers 的 b_前缀归属）。
 */

/** 沉默多久算"比平时久"：实测最长一次连续 2 分 36 秒没有阶段事件。 */
const QUIET_HINT_MS = 150_000
/** 沉默多久从"刚刚"改为"结果尚未返回"。 */
const SETTLED_MS = 15_000

/**
 * 「当前工位」那一行从"正在"改为"上一步是"的阈值。
 *
 * 为什么要跟 QUIET_HINT_MS（150 秒）分开：那个 150 秒是给底部**连接行**用的
 * （「比平时久一些」是一个关于整轮的判断），而主行说的是"此刻在做什么"——
 * 用现在时断言一件两分半钟没有任何证据的事，跟本页"只展示系统确实知道的状态"的纪律冲突。
 *
 * 60 秒的取值理由：实测存在 106 秒的完全沉默区，所以 60 秒以上的空档是**常态**，
 * 这个改口在大多数轮次里都会真的发生（而不是一个永不触发的摆设）。
 * 它宁可早说，因为早说的内容是真的。
 */
const BENCH_STALE_MS = 60_000

/**
 * 「当前工位」那一行该说什么 —— 等待页唯一会由真实事件驱动变化的文案。
 *
 * 这里有一道**沉默闸门**，它是这一版最重要的诚实约束：
 * 实测最长一次连续 2 分 36 秒没有阶段事件。如果没有这道闸门，页面会拿最后那个阶段
 * 一直显示成"正在进行中"——而它在过去两分半里没有收到任何新消息，
 * 那就是在替后端编它没有说过的话。
 *
 * 所以沉默超过阈值后必须**改口**：不再说"正在核对…"，而是如实说没有新信息。
 * 最后那个阶段不会丢，它留在下面的痕迹行里（事实是"发生过"，不是"正在发生"）。
 *
 * quiet 同时被用来给节点换 key —— 改口那一刻也播一次入场，因为页面确实变了。
 */
export function benchLine(run: RunState): { text: string; key: string; quiet: boolean } {
  if (run.orphaned) {
    return run.orphanCause === 'cancelled'
      ? { text: '这一轮已经取消', key: 'cancelled', quiet: false }
      : { text: '这一轮已经中断', key: 'left', quiet: false }
  }
  if (run.status === 'failed') {
    return { text: '这一轮没有成功', key: 'failed', quiet: false }
  }
  // 原文案是「已接单」——那是外卖/快递的语言，一个做饭的页面说"接单"是错位的。
  if (!run.currentStage) {
    return { text: '你的要求已经收到，正在开始', key: 'start', quiet: false }
  }
  const last = run.events.length > 0 ? run.events[run.events.length - 1].at : 0
  if (run.elapsed - last >= BENCH_STALE_MS) {
    // 保留事实、只改时态。初版这里是「后台仍在处理，暂时没有新的阶段信息。」——
    // 那一句确实诚实，但它把"刚才在做核对"这个**我们真的知道的事**一起丢掉了，
    // 用户于是拿不到任何信息。现在改成说清"上一步是什么"+"还没有新的"。
    return {
      text: `上一步是${stagePhrase(run.currentStage)}，还没有新的进展。`,
      key: 'stale',
      quiet: true,
    }
  }
  return { text: stageSentence(run.currentStage), key: run.currentStage, quiet: false }
}

/** 底部连接行。心跳只能证明连接，绝不证明工作前进了一格（lave 两席一致点名）。 */
export function connLine(run: RunState): { text: string; tone: 'ok' | 'quiet' | 'warn' } {
  if (run.orphaned) {
    return {
      text:
        run.orphanCause === 'cancelled'
          ? '你取消了这一轮 · 连接已断开'
          : '已中断 · 页面离开时连接断开',
      tone: 'warn',
    }
  }
  if (run.status === 'failed') return { text: '这一轮没有成功', tone: 'warn' }
  if (isStale(run)) {
    return { text: `已 ${formatSpoken(sinceLastHeartbeat(run))} 没有心跳，可能已断`, tone: 'warn' }
  }
  const last = run.events.length > 0 ? run.events[run.events.length - 1].at : 0
  const quiet = run.elapsed - last
  if (quiet >= QUIET_HINT_MS) return { text: '连接正常 · 这次处理比平时久一些', tone: 'quiet' }
  if (quiet >= SETTLED_MS || run.elapsed >= SETTLED_MS) {
    return { text: '连接正常 · 结果尚未返回', tone: 'ok' }
  }
  return { text: '连接正常 · 菜单尚未确定', tone: 'ok' }
}

/** 事件到达时刻换算成"开跑后第几分几秒"。卷带没了，但时刻本身是真实数据，不该丢。 */
export function clockOf(atMs: number): string {
  const total = Math.floor(atMs / 1000)
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}
