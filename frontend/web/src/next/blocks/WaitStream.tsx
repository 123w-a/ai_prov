import { useEffect, useRef } from 'react'
import type { RunState } from '../data/model.ts'
import { renderRichText } from '../../utils/richText.tsx'
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
  const bodyRef = useRef<HTMLDivElement>(null)
  /** 是否贴着底部。用户主动往上翻看已写内容时置 false，之后不再把他拽回来。 */
  const stickRef = useRef(true)

  useEffect(() => {
    const el = bodyRef.current
    if (!el || !stickRef.current) return
    /* 瞬时赋值，而不是 scrollTo({ behavior: 'smooth' })：
     * 流式每秒追加十几次，平滑动画会被下一次更新打断，反而抖成一团。
     * 旧前端 ChatArea 用 smooth 是因为它按「消息」更新（一轮一次），频率完全不同；
     * 那里照搬过来的只有「贴底判定 + 80px 余量」，滚动行为本身必须换。
     *
     * 为什么必须滚：正文一直在往末尾追加，但容器滚动位置停在 0 ——
     * 用户看到的永远是开头那几行，静止不动，看起来就像生成卡住了。 */
    el.scrollTop = el.scrollHeight
  }, [run.body])

  const handleScroll = () => {
    const el = bodyRef.current
    if (!el) return
    /* 80px 余量沿用旧前端 ChatArea.handleFeedScroll 的口径：差 1px 就判定
     * 「用户在看历史」会让跟随莫名其妙地停掉。 */
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }

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
      <div className="wait-stream-body" ref={bodyRef} onScroll={handleScroll}>
        {/* P1（2026-10-19）：流式正文按富文本渲染（与结果页/旧前端同一套 renderRichText）。
            直出字符串会让 markdown 记号裸露，打字机的"在写一篇文章"的感觉全没了。 */}
        {renderRichText(run.body)}
      </div>
      {run.orphaned && (
        <p className="wait-stream-note">这是没有跑完的过程稿，不构成最终建议。</p>
      )}
    </section>
  )
}
