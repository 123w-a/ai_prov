import type { RunState } from '../data/model.ts'
import { Icon } from '../ui/Icon.tsx'

/**
 * 等待页的「正在生成中…」正文面板（2026-10-12 从 WaitCard 拆出）。
 *
 * 稿把它画成处理详情行下面一块等宽的描边面板：标题一行 + 正文多段，
 * **面板自己滚动**（高约 196px，超出的部分在框内走），页面不再往下长。
 * 这一点是这一版与上一版最要紧的差别：上一版把正文直接铺进页面流，长正文
 * 会把"取消"推到屏幕外——用户等的时候连出口都看不见。
 *
 * 标题按状态改口（异常态稿没画，这里不套用正常态的话）：
 *   · 正在跑 —— 「正在生成中…」
 *   · 被取消 / 断线 —— 「取消前/中断前已经写出的内容」
 * 正常态不加字数、不加"这只是过程稿"那句：稿上没有，而"菜单还没定"由大标题
 * 承担。异常态必须加——那时用户看到的是一段**只写了一半**的文字，不说明
 * 就会被当成结论。诚实性优先于版面一致性，这一处是唯一的有意偏离。
 */
export function WaitStream({ run }: { run: RunState }) {
  if (!run.body) return null

  const cancelled = run.orphanCause === 'cancelled'
  return (
    <section className="wait-stream">
      <h3 className="wait-stream-title">
        <Icon name="doc" />
        {run.orphaned
          ? cancelled
            ? '取消前已经写出的内容'
            : '中断前已经写出的内容'
          : '正在生成中…'}
      </h3>
      <div className="wait-stream-body">{run.body}</div>
      {run.orphaned && (
        <p className="wait-stream-note">这是没有跑完的过程稿，不构成最终建议。</p>
      )}
    </section>
  )
}
