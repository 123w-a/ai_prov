import type { RunState } from '../data/model.ts'
import type { FamilyMemberRow, WeekView } from '../data/firstScreen.ts'
import { AskRail } from '../blocks/AskRail.tsx'
import { AskComposer } from '../blocks/AskComposer.tsx'

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

          {/* 书写面抽成了 AskComposer：结果页底部用的是同一个组件（见 ResultView）。
              两处必须共用一份行为定义，否则回车键、禁用条件、按钮文案会各自漂移——
              用户看到的就是"这一页的输入框跟刚才那个不一样"。 */}
          <AskComposer
            text={text}
            onText={onText}
            onSend={onSend}
            running={running}
            blocked={blocked}
            submitLabel={run.status === 'failed' ? '重试' : undefined}
          />
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


