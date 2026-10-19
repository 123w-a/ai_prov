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
  if (visible.length === 0) return null
  // formatStamp 的 now 由调用方传入、这里只取一次：整列表共用同一"此刻"，
  // 否则跨分钟渲染的两条会显示成两个"今天"。
  const now = Date.now()

  return (
    <div className="hs-list">
      {visible.map((t) => (
        <article key={t.key} className="hs-turn">
          <header className="hs-q">
            <div className="hs-qmeta">
              {t.askedAt !== null && (
                <time className="hs-time" dateTime={new Date(t.askedAt).toISOString()}>
                  {formatStamp(t.askedAt, now)}
                </time>
              )}
              {t.cancelled && <span className="hs-chip">已取消</span>}
              {!t.cancelled && t.unfinished && <span className="hs-chip">未完成</span>}
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

          {t.answerKind === 'structured' || t.answerKind === 'prose' ? (
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
      ))}
    </div>
  )
}
