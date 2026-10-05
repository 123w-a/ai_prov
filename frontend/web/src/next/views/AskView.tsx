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
/**
 * 首屏示例问法（2026-10-05）。
 *
 * 选句标准：四句覆盖四种**不同的提问起点**——按口味（想吃鱼清淡）、按人数与
 * 忌口（两个人吃别太油腻）、按现有食材（只有鸡蛋和西兰花）、按做法诉求
 * （想喝汤、简单快手）。不写"帮我推荐晚餐"这种空话：示例问法的价值在于让用户
 * 看到"原来可以问到这么具体"，一句泛泛的示范反而教不会他怎么问。
 */
const EXAMPLES = [
  '今晚想吃鱼，清淡一点',
  '两个人吃，别太油腻',
  '冰箱里只有鸡蛋和西兰花',
  '想喝点汤，简单快手的',
]

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
            {/* 篇首图形（2026-10-05，用户要求"更具体的图形"，由 lave1 的设计师模型出稿）。
                造型＝一只碗 + 一双斜搭的筷子，不重复文案里的"两个人"——那已由 em 强调。
                **这里是矢量重绘而不是位图**：设计师交付的是 1024² 位图线稿，缩到 52px 后
                线条淡到几乎看不见（64px 最近邻放大预览实测已证），而 SVG 在任何尺寸都
                保持 1.7 单位线宽，并且能用 currentColor 跟令牌走色。构图沿用设计师选定的
                那一版，没有另起一套造型。 */}
            <svg
              className="ask-lead-art"
              viewBox="0 0 64 64"
              aria-hidden="true"
              focusable="false"
            >
              <g
                fill="none"
                stroke="currentColor"
                strokeWidth="1.7"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M11 22c0 16 8.5 28 21 28s21-12 21-28" />
                <ellipse cx="32" cy="22" rx="21" ry="6.2" />
                <path d="M24.5 50.2l1.6 5.3h11.8l1.6-5.3" />
                {/* 筷子两端各伸出碗口 8 单位。第一版只伸出 2 单位，两根筷子看起来是
                    "画在碗口里的两道斜线"，读不出"搁在碗沿上"；伸出量是这个造型的
                    语义所在，不能省。 */}
                <path d="M3 16L61 27" />
                <path d="M3 20.5L61 31.5" />
              </g>
            </svg>
            今晚这一顿，按<em>两个人的身体</em>来定。
            {/* 卡片右上角的蒸笼（同一位设计师的 G 稿，同样是矢量重绘）。
                它用 --line-strong 而非 --accent，原因见 base.css 里那条规则的注释：
                左上角的碗筷是主角图形，这一枚是静物背景，同色同重会互相抢。
                挂进 .ask-lead（它已是 position:relative）而不是 .ask-main——
                .ask-main 是 flex 纵向容器且是 static，SVG 走流会占掉垂直空间。 */}
            <svg
              className="ask-aside-art"
              viewBox="0 0 236 94"
              aria-hidden="true"
              focusable="false"
            >
              {/* 一杆中式戥子（lave2 / claude-opus-5-5 第二轮商讨裁定，2026-10-05）。
                  上一版"两只碗 + 一双横箸"被用户否决，原话：「一个家庭不可能只有
                  两个人，如果再添加家庭成员了，那图画又不会跟着变动。所以重新设计，
                  不要困死在有没有碗里面。」——死因是把"2"写死进了图形，它依赖标题里
                  "两个人"，标题一变三个人，图形立刻自相矛盾。
                  这一轮的转法：上一版画的是**名词**（碗＝人），人数一变即成谎言；
                  这一版画**动词**——戥子称的是"因人配量"这个动作，不是"称了谁"。
                  硬约束：全图不得出现任何可数的东西——所以钩上那撮香草必须画成
                  疏密不匀的线束、绝不成颗粒（颗粒即可数，可数即人数），不画第二只盘、
                  不画一组砝码、不画等距刻度（秤星会被读成"一格一个人"）。
                  用钩不用盘：秤盘是开口朝上的浅容器，与左侧那只碗同形，等于把碗
                  画第二遍。钩上必须有物——否则秤会被读成"称人"。
                  不单调靠三件与数量无关的事：①轴冲突（秤杆是斜线、钩绳是垂线，
                  都不平行于格子横轴）②密度梯度（支点处密、杆梢处空）③偏心落点
                  （唯一加密处是提纽，且明显偏左）。 */}
              <g
                fill="none"
                stroke="currentColor"
                strokeWidth="1.4"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                {/* 秤杆：两条边构成锥形（近手端粗、杆梢细），斜率不同故不是平行长线 */}
                <path d="M30 70L204 21" />
                <path d="M30 75L204 22.5" />
                {/* 提纽：唯一支点，偏左。环下一个实心结，是整根杠杆的出发点 */}
                <circle cx="88" cy="53" r="4.5" />
                <circle cx="88" cy="53" r="1.8" fill="currentColor" />
                {/* 秤砣：近手端（短臂）。第一版画成上宽下窄的梯形，在 236px 宽里读起来
                    像**一只小碗**——正撞本轮"不要困死在碗里面"的死线。改成戥子实际
                    所用的圆柱形（侧视即矩形），与碗彻底脱钩。 */}
                <path d="M30 73v6" />
                <path d="M25 79h10v9H25z" />
                {/* 秤钩：另一头（长臂）垂下，末端弯钩 */}
                <path d="M204 22v38" />
                <path d="M204 60c0 8-4 12-10 12" />
                {/* 钩下垂的一小撮香草：第一版只有四条细线，真实尺寸下细到读不出，整幅
                    又滑回"淡痕"。加密到六条长短方向不一的线束加两片叶，读作"一团"而
                    不是"几粒"——颗粒即可数、可数即人数，这条死线不能破。 */}
                <path d="M194 72c-3 6-8 10-14 12" />
                <path d="M194 72c0 7-2 12-4 16" />
                <path d="M194 72c3 6 7 10 12 11" />
                <path d="M194 72c-1 8-5 13-11 16" />
                <path d="M194 72c4 4 9 6 14 6" />
                <path d="M194 72c-5 5-11 7-16 7" />
                <path d="M188 82c-3-2-6-1-7 2 2 2 5 1 7-2z" />
                <path d="M198 78c3-2 6-1 7 2-2 2-5 1-7-2z" />
              </g>
            </svg>
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

          {/* 示例问法（2026-10-05，用户批准的首屏主卡下半部第一项）。
           *
           * 填的是那片一直空着的下半部。它此前是**未定义区域**而非缺陷：
           * 首屏预期图从来没有画过这一页（只有结果页与候选页），所以那不是
           * "做漏了"，是没定过——这次按用户裁决补上。
           *
           * 只做一件事：把一句能直接用的问法放进输入框，**不替他发出去**。
           * 与候选页药丸同一条纪律（那种句子是半成品，直接发等于替他决定了
           * 没说出口的那一半）。所以点击走 onText，不是 onSend。
           *
           * 四句都刻意**不含人名、不含具体病名**：它们是产品引导，不是演示数据。
           * 写成"小美怀孕了"就变成拿真实家人的名字当样例，既不通用也不体面。 */}
          <ul className="ask-examples">
            <li className="ask-examples-label">试试这样问</li>
            {EXAMPLES.map((t) => (
              <li key={t}>
                <button className="ask-example" type="button" onClick={() => onText(t)}>
                  {t}
                </button>
              </li>
            ))}
          </ul>
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


