import { useState } from 'react'
import type { HistoryTurn } from '../data/historyTurns.ts'
import type { RunState } from '../data/model.ts'
import { EMPTY_RUN, formatStamp } from '../data/model.ts'
import { RunResult } from './ResultView.tsx'

/**
 * 历史会话的**纵向轮次列表**（2026-10-19）。
 *
 * 一个会话里所有问答轮按后端原始顺序从上往下排：用户问题（+上传图）在轮首，
 * 回答在下面。回答**复用现有 RunResult**（构造 RunState 且 history: true），
 * 所以历史里读到的答案版式与实跑结果页完全一致——只是只读（实施单第 3 条的
 * 隐藏清单由 ResultView 按 run.history 统一执行）。
 *
 * 三条纪律：
 *   1. 顺序就是后端给的顺序，不排序、不分组——"纵向看到全部回答"的前提；
 *   2. skipRecordId 排除当前页面已经作为实跑结果渲染的同一后端记录，否则刷新后
 *      同一条问答会出现两次（一次实跑、一次历史）；
 *   3. 状态标注（已取消 / 未完成 / 回答生成中）只标注事实，不补造回答内容。
 */

/** 默认展开最近几轮。实测一屏（feed 493px）只装得下一轮约 515px 的结果，
 *  25 轮全展开是 16000px——想翻到某一轮只能一路划到底。默认只开最近两轮，
 *  其余折成"问句 + 时间"（内容一个字不删，点开即完整）。 */
const OPEN_TAIL = 2

/** 历史轮 → 只读 RunState。答案展示样式与实跑一致，过程账目没有就是没有。 */
function historyRun(t: HistoryTurn): RunState {
  return {
    ...EMPTY_RUN,
    status: 'succeeded',
    request: t.request,
    askedAt: t.askedAt,
    answer: t.answer,
    body: t.body,
    history: true,
  }
}

export function HistoryTurnsView({
  turns,
  skipRecordId,
  onDeleteTurn,
}: {
  turns: HistoryTurn[]
  /** 当前页面已作为实跑结果渲染的后端记录号；命中即从历史列表里排除。 */
  skipRecordId?: number | null
  /** 消息级删除（2026-10-19 从旧前端找回）：删这一轮问答。没落点的轮不给入口。 */
  onDeleteTurn?: (recordId: number) => void
}) {
  const visible = skipRecordId == null ? turns : turns.filter((t) => t.recordId !== skipRecordId)
  // 用户手动改过的轮次记在这里；没记过的按"默认只开最近 OPEN_TAIL 轮"算。
  // 用覆盖表而不是把默认值塞进 state：切换会话时 turns 变了，派生出来的默认值
  // 自动跟上，不需要 useEffect 去重置（那样会闪一帧旧折叠态）。
  const [override, setOverride] = useState<Record<string, boolean>>({})
  if (visible.length === 0) return null
  // formatStamp 的 now 由调用方传入、这里只取一次：整列表共用同一"此刻"，
  // 否则跨分钟渲染的两条会显示成两个"今天"。
  const now = Date.now()
  const foldAll = (v: boolean) => setOverride(Object.fromEntries(visible.map((t) => [t.key, v])))

  return (
    <div className="hs-list">
      {visible.length > OPEN_TAIL && (
        <div className="hs-tools">
          <span className="hs-tools-note">
            共 {visible.length} 轮 · 默认展开最近 {OPEN_TAIL} 轮
          </span>
          <button type="button" className="hs-tool" onClick={() => foldAll(false)}>
            全部展开
          </button>
          <button type="button" className="hs-tool" onClick={() => foldAll(true)}>
            全部收起
          </button>
        </div>
      )}
      {visible.map((t, i) => {
        const hasBody = t.answerKind === 'structured' || t.answerKind === 'prose'
        const isFolded = override[t.key] ?? i < visible.length - OPEN_TAIL
        return (
          <article key={t.key} className="hs-turn" data-fold={isFolded ? '1' : '0'}>
            <header className="hs-q">
              <div className="hs-qmeta">
                {t.askedAt !== null && (
                  <time className="hs-time" dateTime={new Date(t.askedAt).toISOString()}>
                    {formatStamp(t.askedAt, now)}
                  </time>
                )}
                {t.cancelled && <span className="hs-chip">已取消</span>}
                {!t.cancelled && t.unfinished && <span className="hs-chip">未完成</span>}
                {hasBody && (
                  <button
                    type="button"
                    className="hs-fold"
                    aria-expanded={!isFolded}
                    onClick={() => setOverride((m) => ({ ...m, [t.key]: !isFolded }))}
                  >
                    {isFolded ? '展开这一轮' : '收起这一轮'}
                  </button>
                )}
                {onDeleteTurn && t.recordId != null && (
                  <button
                    type="button"
                    className="hs-del"
                    aria-label={`删除这一轮问答：${t.request.slice(0, 12)}`}
                    onClick={() => onDeleteTurn(t.recordId as number)}
                  >
                    删除这轮
                  </button>
                )}
              </div>
              <p className="hs-req">{t.request}</p>
              {t.requestImageUrl ? (
                <img className="hs-qimg" src={t.requestImageUrl} alt="上传的食材图片" loading="lazy" />
              ) : null}
            </header>

            {hasBody && !isFolded ? (
              <RunResult
                run={historyRun(t)}
                onAgain={() => {}}
                text=""
                onText={() => {}}
                onSend={() => {}}
                blocked
                onPickCandidate={() => {}}
                hideInput
              />
            ) : t.answerKind === 'pending' ? (
              <p className="hs-state">回答生成中…</p>
            ) : null}
          </article>
        )
      })}
    </div>
  )
}
