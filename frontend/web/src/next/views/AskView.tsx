import { useState } from 'react'
import type { RunState } from '../data/model.ts'
import type { RecognizedItem } from '../data/fridgeText.ts'
import type { FamilyMemberRow, WeekView } from '../data/firstScreen.ts'
import { leadBodyPhrase } from '../data/viewModel.ts'
import { AskRail } from '../blocks/AskRail.tsx'
import { AskComposer } from '../blocks/AskComposer.tsx'
import { MealCrewPick } from '../blocks/MealCrewPick.tsx'
import { FailureNote } from '../blocks/FailureNote.tsx'
import { OpenNearby } from '../blocks/OpenNearby.tsx'

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
 * 首屏示例问法（2026-10-05；2026-10-10 按首屏方向稿补图标并改一行四列）。
 *
 * 选句标准：四句覆盖四种**不同的提问起点**——按口味（想吃鱼清淡）、按人数与
 * 忌口（两个人吃别太油腻）、按现有食材（只有鸡蛋和西兰花）、按做法诉求
 * （想喝汤、简单快手）。不写"帮我推荐晚餐"这种空话：示例问法的价值在于让用户
 * 看到"原来可以问到这么具体"，一句泛泛的示范反而教不会他怎么问。
 *
 * 图标（设计稿 ask-expect.png）：四张卡各配一枚小物——鱼 / 饭碗 / 西兰花 / 汤碗，
 * 与四句一一对应（图形就是那句话里的东西，不另起隐喻）。**全部 currentColor
 * 描边单色**：稿里的四枚是彩色点，但语义色（红黄绿）只允许落在状态点上
 * （2026-09-28 硬边界），装饰图标不能挪用——所以这里取形状、不取颜色。
 *
 * 2026-10-10 重对版：加 line（**显示用分行**）。卡宽实测内宽只有 ~142px，
 * 12 个汉字必然折行，浏览器默认会在任意位置断（把「一点」「西兰花」拆开）；
 * 稿的断法是在标点/语义处（「今天想吃鱼，/ 清淡一点」）。所以显示走 line、
 * 点击填入仍用 t 原文——**不能把换行符写进 t**，那会把它注进输入框。 */
const EXAMPLES = [
  { t: '今天想吃鱼，清淡一点', line: ['今天想吃鱼，', '清淡一点'], art: 'fish' },
  { t: '两个人吃，别太油腻', line: ['两个人吃，', '别太油腻'], art: 'bowl' },
  { t: '冰箱里只有鸡蛋和西兰花', line: ['冰箱里只有', '鸡蛋和西兰花'], art: 'broc' },
  { t: '想喝点汤，简单快手的', line: ['想喝点汤，', '简单快手的'], art: 'soup' },
] as const

/**
 * 四枚小物（2026-10-11：由手绘 16×16 线稿改为**从稿切片**的彩色位图）。
 *
 * 界面里只留一个 span + 一个 class（art-fish / art-bowl / art-broc / art-soup），
 * 图在 firstscreen.css 里按 class 挂 src/next/assets/hero/icon-1..4.png。
 * 切片的 maskIoU 0.944~0.995（见 assets/hero/manifest.json），也就是形状与稿逐像素
 * 一致；颜色也一并来自稿——原来的单色描边版在稿面前是"形状像、颜色不像"。
 *
 * 与 2026-09-28「语义色（红黄绿）只落在状态点」的关系，写清楚免得下一个人踩：
 * 稿这四枚用的确实是青/陶/绿/黄（鱼=青、碗=陶、西兰花=绿、汤=黄），但它们是
 * **食物本身的固有色**，不是状态编码；状态色仍只出现在右栏的圆点（营养状况）。
 * 这一条是本次改动里唯一跨了旧边界的地方，已向用户标注、可由用户一句话回退
 * （回退 = 只改这 4 行 background-image，形状与布局不动）。
 */

export function AskView({
  run,
  text,
  onText,
  onSend,
  running,
  blocked,
  onIngredients,
  fam,
  week,
  cand,
  fridge,
  readErr = null,
  crew,
  onRetry,
}: {
  run: RunState
  text: string
  onText: (next: string) => void
  onSend: () => void
  running: boolean
  blocked: boolean
  /** 拍照识别出的结构化清单，随本轮发送带回后端（2026-10-22）。
   *  与 text/onText 同层传递——食材要跟着 /api/chat 发出去，状态必须待在
   *  发起请求的那一层，本组件只接值与回调（边界同 crew）。 */
  onIngredients?: (items: RecognizedItem[]) => void
  fam: { members: FamilyMemberRow[]; shared: string[] } | null
  week: WeekView | null
  cand: { member: string; source: string; severity: string } | null
  fridge: { count: number; names: string } | null
  /** 首屏读取失败的原因（useFirstScreen().readError）。透传给右栏，不在这里加工。 */
  readErr?: string | null
  /** 本餐用餐人（2026-10-15 用户裁决）。打包成一个对象，理由有二：
   *  ① 它是**一件事的五个面**（已选/已确认/三个动作），散成五个 prop 会让
   *     这个组件的签名变成一长串彼此无关的字段；
   *  ② 状态必须待在发起请求的那一层（`members` 要跟着 /api/chat 发出去），
   *     这里只接值与回调，与 AskView 「只渲染、不做 I/O」的边界一致。
   *
   *  `null` = 家里成员还没读出来（或一位都没有）⇒ **选择区不渲染，也不设关卡**。
   *  这一条是有意为之：不能让"读不到成员"把整个首屏锁死——那时用户连问都问不了。
   *  读失败该由右栏那块「有数据没读出来」负责说明，不是靠卡住书写面表达。 */
  crew: {
    picked: string[]
    confirmed: boolean
    onToggle: (name: string) => void
    /** 全选 / 清空：已选人数等于成员总数时清空，否则选齐。 */
    onSelectAll: () => void
    onConfirm: () => void
    onEdit: () => void
  } | null
  /** 失败块的「再试一次」：把这一轮原本的话重发一遍（run.request）。
   *  实现在 app 层（它才持有 send 与请求上下文），这里只接一个回调。
   *  不传 = 只显示说明与原文——夹具与只读场景用得上。 */
  onRetry?: () => void
}) {
  /* 附近餐厅是否展开。它只影响**示例卡在与不在**这一件事：
     展开时示例卡就地让位（同一位置互换），收起时原样回来——
     版心骨架与书写面一个像素都不动（见 blocks/OpenNearby.tsx 头注释）。 */
  const [nearbyOpen, setNearbyOpen] = useState(false)

  /* 关卡是否落下：选人区在场、且还没确认。两个条件缺一不可——
     `crew` 为 null 表示成员没读出来，那种情况不设关卡（理由见 crew 的注释）。 */
  const gateLocked = crew !== null && !crew.confirmed

  return (
    <section className="starter">
      {run.status === 'failed' && <FailureNote run={run} onRetry={onRetry} />}

      {/* 2026-09-27 用户裁决：首屏从「标题→输入→按钮→脚注」的纯垂直堆叠
          改成左右两栏，右栏放身份确认。主因是布局（lave 与我一致：元素太少
          不是问题，任务本来就该克制），不是补内容。
          网格模板与右栏宽度**复用 .result-body 那套**（minmax(0,1fr) var(--rail,278px)、
          gap 0 40px）——三视图右栏必须同宽，另造一个 260px 就是新的几何不一致。 */}
      <div className="ask-layout">
        <div className="ask-main">
          <h2 className="ask-lead">
            {/* 2026-10-10 设计稿重对版：标题两侧的**旧元素整体退场**——
                ① 碗筷篇首图形（10-05 旧稿要求"更具体的图形"）与
                ② 右上菜篮位图（P2 稿直接用位图）在新稿里都不存在，
                新稿标题是**纯文字**、图形氛围由页面级叶饰承担（右上大枝、
                左下大丛、页头花章线）。用户全盘认可新稿 ⇒ 稿里没有的
                旧装饰按退场处理，不是"删功能"，是清残留。 */}
            今天吃什么，按<em>{leadBodyPhrase(fam?.members.length)}</em>来定。
          </h2>

          {/* 本餐用餐人（2026-10-15）：放在书写面**之上**——先定这一顿谁吃、
              再写想吃什么，上下顺序本身就是语义。它只在家里至少有一位成员、
              且成员已读出来时才出现；否则不设关卡（见 crew 的注释）。 */}
          {fam && crew && (
            <MealCrewPick
              members={fam.members}
              picked={crew.picked}
              onToggle={crew.onToggle}
              onSelectAll={crew.onSelectAll}
              confirmed={crew.confirmed}
              onConfirm={crew.onConfirm}
              onEdit={crew.onEdit}
            />
          )}

          {/* 书写面抽成了 AskComposer：结果页底部用的是同一个组件（见 ResultView）。
              两处必须共用一份行为定义，否则回车键、禁用条件、按钮文案会各自漂移——
              用户看到的就是"这一页的输入框跟刚才那个不一样"。 */}
          {/* 关卡（2026-10-15）：**只拦"还没确认"，不拦"确认了但一个都没选"**。
             后者是一个有意义的回答（这一顿不登记用餐人、不套约束），后端收到空数组
              就不写 members 键。成员没读出来（crew 为 null）时同样不拦。
                 未确认时**不是**把输入框设成 disabled：那样用户会对着一个打不进字的
              框发呆，不知道缺的是哪一步。这里换成一句说明，缺哪步就写哪步。 */}
          {gateLocked ? (
            <p className="ask-gate">先确认这一顿是谁吃，就能开始写了。</p>
          ) : (
            <AskComposer
              text={text}
              onText={onText}
              onSend={onSend}
              running={running}
              blocked={blocked}
              onIngredients={onIngredients}
              submitLabel={run.status === 'failed' ? '重试' : undefined}
            />
          )}

          {/* 「出去吃」入口条（2026-10-12）：书写面**正下方、通栏**，
              在「试试这样问」与示例卡**之上**。它自己持展开状态与查询，
              展开时通过 onOpenChange 通知本视图把示例卡收起来。 */}
          <OpenNearby draft={text} onOpenChange={setNearbyOpen} />

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
          {/* 2026-10-10：设计稿是**一行四列、每卡一枚小物在上**——四卡并排。
              窄屏的列数在 firstscreen.css（900 断点两列、560 以下一列）。 */}
          {nearbyOpen ? null : (
            <ul className="ask-examples">
              <li className="ask-examples-label">试试这样问：</li>
              {EXAMPLES.map(({ t, line, art }) => (
                <li key={t}>
                  <button className="ask-example" type="button" onClick={() => onText(t)}>
                    <span className={`ask-example-art art-${art}`} aria-hidden="true" />
                    <span className="ask-example-t">
                      {line.map((s) => (
                        <span key={s}>{s}</span>
                      ))}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>{/* /ask-main */}

        {/* 首屏四块（IA 薄切片 · 契约 v2.1）。
         *
         * 存在性判断在 AskRail 里：四块全部没数据时整栏不渲染，不留空框。
         * 右栏宽度与网格仍复用 .result-body 那套，本切片**不改**任何视觉 token，
         * 也不做 19px 区块标题/22px 大数字（那是 lave 裁定的视觉项，IA 之后再做）。 */}
        <AskRail fam={fam} week={week} cand={cand} fridge={fridge} readErr={readErr} />
      </div>{/* /ask-layout */}
    </section>
  )
}








