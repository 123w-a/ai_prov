import type { RunState } from '../data/model.ts'
import type { FamilyMemberRow, WeekView } from '../data/firstScreen.ts'
import { AskRail } from '../blocks/AskRail.tsx'

/**
 * 「今晚这一顿」的提问视图。
 *
 * 2026-09-27 从 app/TonightApp.tsx 拆出。拆之前那一版是「外壳 + 提问表单 + 状态机」
 * 三样挤在一个文件里（398 行），AskView 独立后外壳只留状态与视图切换。
 *
 * 职责边界：**只渲染，不做任何 I/O**。会话创建、流式请求、存档、中断全在 app 层；
 * 这里收到的全是已经就绪的值，改动一律经回调上抛。
 * 档案与冰箱也**不许**在这里自己拉——它们是 app 用同一份数据算好的，
 * 提问页与等待页必须读到同一份，否则两页会显示不同的人。
 *
 * 2026-09-28 右栏从「Panel 里两行 12.5px 淡字」换成四块 IA 骨架（契约 v2.1 §1）：
 * 待确认 → 家庭 → 本周 → 冰箱。原来那版把家庭和冰箱压成两行小字，
 * 是 Claude 视觉诊断视觉诊断①④指认的「右栏把信息压扁、不成层次」的实证。
 * 数据推导全在 app 层（firstScreen.ts），这里只负责排版与存在性装配。
 */
export function AskView({
  run,
  text,
  onText,
  onSend,
  running,
  blocked,
  fam,
  week,
  cand,
  fridge,
}: {
  run: RunState
  text: string
  onText: (next: string) => void
  onSend: () => void
  running: boolean
  blocked: boolean
  fam: { members: FamilyMemberRow[]; shared: string[] } | null
  week: WeekView | null
  cand: { member: string; source: string; severity: string } | null
  fridge: { count: number; names: string } | null
}) {
  return (
    <section className="starter">
      {run.status === 'failed' && (
        <div className="failure">
          <strong>这一轮没有成功</strong>
          <p>{run.error}</p>
          {run.httpStatus !== undefined && <p className="mono">HTTP {run.httpStatus}</p>}
          {run.body && (
            <details className="failure-partial">
              <summary>查看已经收到的部分内容（可能不完整）</summary>
              <pre>{run.body}</pre>
            </details>
          )}
        </div>
      )}

      {/* 2026-09-27 用户裁决：首屏从「标题→输入→按钮→脚注」的纯垂直堆叠
          改成左右两栏，右栏放身份确认。主因是布局（lave 与我一致：元素太少
          不是问题，任务本来就该克制），不是补内容。
          网格模板与右栏宽度**复用 .result-body 那套**（minmax(0,1fr) var(--rail,278px)、
          gap 0 40px）——三视图右栏必须同宽，另造一个 260px 就是新的几何不一致。 */}
      <div className="ask-layout">
        <div className="ask-main">
          <h2 className="ask-lead">
            今晚这一顿，按<em>两个人的身体</em>来定。
          </h2>

          <label className="ask">
            <span className="ask-label">你想吃什么</span>
            {/* 2026-10-04：给书写面套一层容器，用来承载印刷语汇（四角裁切标记 +
                引导弧线）。装饰全部画在这层的伪元素上，textarea 本身一字符未改。 */}
            <span className="ask-field">
              <textarea
                className="ask-input"
                value={text}
                rows={2}
                disabled={running}
                onChange={(e) => onText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) onSend()
                }}
              />
            </span>
          </label>

          <div className="ask-actions">
            <span className="ask-hint">
              一句话就够。小膳管家会结合家人的健康情况来定这一顿。
            </span>
            <button className="ask-go" type="button" disabled={blocked || !text.trim()} onClick={() => onSend()}>
              {run.status === 'failed' ? '重试' : '开始'}
            </button>
          </div>
        </div>{/* /ask-main */}

        {/* 首屏四块（IA 薄切片 · 契约 v2.1）。
         *
         * 存在性判断在 AskRail 里：四块全部没数据时整栏不渲染，不留空框。
         * 右栏宽度与网格仍复用 .result-body 那套，本切片**不改**任何视觉 token，
         * 也不做 19px 区块标题/22px 大数字（那是 lave 裁定的视觉项，IA 之后再做）。 */}
        <AskRail fam={fam} week={week} cand={cand} fridge={fridge} />
      </div>{/* /ask-layout */}
    </section>
  )
}

