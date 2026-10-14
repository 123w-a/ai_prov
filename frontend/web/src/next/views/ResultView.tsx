import { useState } from 'react'
import type { RunState } from '../data/model.ts'
import { formatClock, formatSpoken, resultForm, stageLabel } from '../data/model.ts'
import { AskComposer } from '../blocks/AskComposer.tsx'
import { renderRichText } from '../../utils/richText.tsx'
// 正文清洗已迁到 data/clean.ts（架构方案第八节第 3 步）。
// 这里只导入、不重新实现——那些正则的 bug 全都是看真实数据才发现的，
// 它们值得被 node --test 直接喂样本，而不是埋在 795 行的组件里。
import { cleanOpening, guardText, sameSource } from '../data/clean.ts'
// 视图模型：字段"在不在、空不空、坏没坏"的判断全部搬到这里（架构方案第三节）。
// 接线之后 Result.tsx 不再直接读 answer 的字段，只读 vm——这一步的判据是
// 「渲染结果与迁移前逐元素完全一致」，由 `小膳当家-前端重建\基线-逐元素样式.json` 比对。
import type { GuardVM, LightVM, ResultVM } from '../data/viewModel.ts'
import { SafetyNotice } from '../blocks/SafetyNotice.tsx'
import { SideRail } from '../blocks/SideRail.tsx'
import { AskEcho } from '../blocks/AskEcho.tsx'
import { NutritionTable } from '../blocks/NutritionTable.tsx'
import { Steps } from '../blocks/Steps.tsx'
// ChapterRail / ChefTip / Members / Sources 已随 2026-10-08 预期图重排摘出中区：
// 章节轨与左栏叠影且预期图没有它；重点与家人适配迁入右栏；中区 Sources 与右栏
// 依据同源重复。组件文件保留（其它形态仍可能复用），这里只删引用。
import { ExtraRecipes } from '../blocks/ExtraRecipes.tsx'
// 候选页右栏（2026-10-05 第 F 项）：复用首屏那三块（档案/本周/冰箱）。
// 数据由 app 层算好传下来——AskRail 自己是只读展示组件，不许在这里再拉一份，
// 否则"首屏说的人"和"候选页说的人"会变成两份可能不一致的数据。
import { AskRail } from '../blocks/AskRail.tsx'
import type { FamilyMemberRow, WeekView } from '../data/firstScreen.ts'
import { Fold } from '../ui/Fold.tsx'
import { Icon } from '../ui/Icon.tsx'
import { DishHero } from '../blocks/DishHero.tsx'
import { buildProseVM, buildResultVM, isShown } from '../data/viewModel.ts'
import { parseCandidateList, stripCandidateLines } from '../data/candidates.ts'
import { CandidateCards } from '../blocks/CandidateCards.tsx'
// 页底动作条（★收藏/赞/踩）的状态与写动作全在 data 层，这里只负责画。
import { useResultActions } from '../data/resultActions.ts'

/**
 * 结果区。**第三版**（2026-09-22，按用户挑定的设计稿 B2 重排）。
 *
 * ═══ 为什么有第三版 ═══════════════════════════════════════════════════════
 *
 * 第二版是九块等重 h2 平铺 → 改成「A 首屏 / B 滚动 / C 折叠 / D 置底」，
 * 用户裁决：「还是不符合我的预期，这个排版和视觉效果都不符合效果」。
 *
 * 与 lave 三席（视觉诊断 / 信息结构 / 对抗席）合议后，根因判为：
 *   **主语错了。** 前两版都在展示「系统生成了一份文档」，而不是回答
 *   「今晚吃什么、为什么适合我们这两个人」。首屏必须先给结论，再给依据。
 *
 * 而实测数据把 lave 方案里的三件东西否决了，不能照做：
 *   ✗ 「页面唯一的盒子 = 家人双栏对照」——dish_matrix/member_adjustments 只有 1/23 有数据，
 *     那个盒子 96% 的时候会空着。它不能当承重结构，只能"有则优雅出现"。
 *   ✗ 「3 个营养大数值」——health_lights 只有 label/level/reason，**没有任何数值字段**。
 *     照做就是逼我编造数字。拒绝。
 *   ✗ 「三步做法」——实测 steps 是 3~8 条，截成 3 步就是丢真实内容。
 *   ✗ 「拼图检测」——元数据里没有能判定拼图的字段。可用的可信信号只有
 *     image_ai_generated（14/23 为 true）与 image_note（22/23 有内容，
 *     且几乎都在承认"这不是真照片"）。改为统一 16:9 裁切 + 如实显示 note。
 *
 * ═══ 第三版的骨架（对齐设计稿 B2）═══════════════════════════════════════════
 *
 *   页眉        小膳管家 · 今晚这一顿            已完成
 *   首屏        通栏菜照（16:9 裁切，无图则整块不渲染）
 *               居中超大菜名
 *               回声：按你「今晚的原话」来配          ← 不回声这页就跟"今晚"无关
 *               指标一行：难度 · 营养 · 用时           ← 全是真实字段
 *   首屏        红色安全条（仅真存在 red 时）        ← 对抗席红线：必须首屏、不得折叠
 *   首屏        「这一碗的重点」= chef_tip            ← 23/23 有数据，是"为什么适合我们"最密的载体
 *   首屏        家人两列（仅当 dish_matrix/member_adjustments 有数据）
 *   滚动        营养小灯（绿/黄/红点 + 词 + 一句理由）
 *   滚动        做法：大号编号 + 真实步数（不截断）
 *   折叠        查看完整说明（opening 全文）          ← 首屏给结论，文档收进折叠
 *   折叠        本轮依据
 *   折叠        真实过程（用时/查证/依据的账目移到这里）
 *   页底        再做一顿（描边，不抢视线）
 *
 * ═══ 两条不许破的规矩 ═════════════════════════════════════════════════════
 *
 * 1. **共享尊严。** 22/23 有卡片、14/35 是纯散文，而用户无法选择拿到哪一种。
 *    卡片若明显更"高级"，拿到散文的那次会被读成次品——那是把后端的不确定性
 *    转嫁成用户对自己这顿饭的失望。两条路径共用同一条阅读宽度、同一套字号档位、
 *    同一套安全提醒处理。**差别只表达信息形态不同，绝不表达系统等级不同。**
 * 2. **不许补造。** 散文里没有菜谱就是没有。不许为了让它"看起来同样高级"
 *    而伪造菜品、营养数值或家人调整。
 */

/* 原有的三个局部类型（MatrixRow / Light / Guard）已随判断逻辑一起搬进 viewModel.ts。
   这里刻意不留别名：留着它就等于给"直接读后端字段"留了后门。 */

/**
 * 护栏状态词只做翻译，不发明。实测 guardrails.status 的取值全是 "pass"（共 3 条）。
 *
 * `pass` 返回空串是刻意的：它的 reason 本身就是「已符合高血压膳食原则」，
 * 再挂一个「已符合」就是同一句话说两遍（`高血压 已符合 · 已符合高血压膳食原则`）。
 * 未知取值原样透出——绝不把它猜成某个已知档位。
 */
/* 已迁到 data/clean.ts：guardText / usableNote。 */

function Prose({ text }: { text: string }) {
  return <div className="prose">{renderRichText(text)}</div>
}

/** 真正的答案正文。首屏只放结论，这份文档收进折叠，但必须可达。 */
function OpeningFold({ opening, body }: { opening: string; body: string }) {
  const showBody = Boolean(body.trim()) && !sameSource(body, opening)
  if (!opening && !showBody) return null
  // 这里**刻意不显示字数**。之前挂的是 opening 的字符数（实测这条是 1220），
  // 那是系统账目，用户不需要知道一篇说明有多少字；上一版就是因为把
  // 用时/查证/依据这类内部计数摆到台面上才被否掉的。
  // 「本轮依据 2」那种计数保留，因为它说的是"有几个来源"，不是"有多少字"。
  return (
    <Fold summary="查看完整说明">
      {opening && <Prose text={opening} />}
      {/* body 与 opening 同源时不再印一遍——实测两者是同一篇（STRUCTURE_PROMPT 规则 1）。 */}
      {showBody && (
        <>
          <h4 className="fold-heading">补充</h4>
          <Prose text={body} />
        </>
      )}
    </Fold>
  )
}

function StructuredResult({ vm, body, onRequestImage, askedAt }: {
  vm: ResultVM
  body: string
  /** 见 RunResult：无落点时上层已置 undefined，这里只管透传。 */
  onRequestImage?: (dishName: string, revision: boolean) => void
  /** 本轮询问时刻（回声条同款）。右栏列尾 meta 用；无时刻不渲染（AskEcho 纪律 3）。 */
  askedAt?: number | null
}) {
  // 这一版只做一件事：把原来散在这里的判断换成读 vm。判断本身一个字没改，
  // 全部照抄进了 viewModel.ts（含"空右栏整块不渲染"这种踩过坑才补上的外层条件）。
  const sources = vm.sources

  return (
    <div className="result-body">
      {/* 章节轨已摘（2026-10-08 预期图 result-expect.png 重排）：预期图没有页内章节导航，
          且它 absolute 定位在左侧留白，与 Shell 左竖栏在同一视野里叠成两套导航影子。 */}

      {/* 右栏条件放宽到「任一段有数据」：重排后右栏是四段（关键/家人适配/注意/依据），
          其中关键与家人不再在中区兜底——只看灯与依据的旧判据会把 23/23 都有的
          「这次搭配的关键」误裁掉。空段仍由各段自判不渲染，右栏不会出现空圆角框。 */}
      {(isShown(vm.rail) || isShown(vm.chefTip) || isShown(vm.members)) && (
        <SideRail vm={vm} sources={sources} askedAt={askedAt} />
      )}

      <div className="result-main">
      {/* ── 首屏 ────────────────────────────────────────────────────── */}

      {/* 报头 + 两列（图 ｜ 简介与营养表）一次给足：营养表作为 children 落在右列
          （预期图里它在图右侧、简介之下），不再通栏铺开。
          锚点 id 保留：折叠区的滚动目标还在，摘掉的只是章节轨本身。 */}
      <div id="sec-dish">
        <DishHero vm={vm} onRequestImage={onRequestImage}>
          <NutritionTable vm={vm} />
        </DishHero>
      </div>

      {/* 重点（chefTip）与家人（members）已迁入右栏 SideRail 第一、二段——
          预期图里「为什么适合我们」与「每个人的适配」同在右栏一列读完。
          红/黄判据本就在右栏，首屏可见、绝不折叠的性质不变。 */}

      {/* ── 滚动 ────────────────────────────────────────────────────── */}

      {isShown(vm.steps) && (
        <div id="sec-steps">
          <Steps steps={vm.steps.data} seasonings={vm.seasonings} ingredients={vm.ingredients} />
        </div>
      )}

      {/* ── 折叠 ────────────────────────────────────────────────────── */}

      <div id="sec-fold">
        <OpeningFold opening={isShown(vm.opening) ? vm.opening.data : ''} body={body} />
      </div>

      {isShown(vm.rest) && <ExtraRecipes recipes={vm.rest.data} />}

      {/* 中区 Sources 已摘：右栏「本轮依据」与它同源（同一份带出处的护栏），
          两处都印等于把同一列指南印两遍。 */}
      </div>
    </div>
  )
}

/**
 * 纯散文结局。
 *
 * 实测 14/35 是这种形态，而用户拿不到哪一种由后端决定。所以它享有和卡片**一样**的
 * 首屏待遇：同样以一句大号结论开场，同样有安全提醒的位置，同样一条阅读宽度。
 *
 * 但它**没有**配图、菜品条目、营养灯——那就没有。不补造。
 * 首行如果是 Markdown 标题，它本来就承担着标题的角色，把它提到标题位、不再在正文里重复。
 */
function ProseResult({ body, request, reds, guards }: {
  body: string
  request: string
  reds: LightVM[]
  guards: GuardVM[]
}) {
  const vm = buildProseVM(body, request)

  if (isShown(vm.empty)) {
    return (
      <p className="prose-empty">
        这一轮后端没有送来可读的建议正文，也没有结构化卡片。
        这是一次真实的空结果，不是页面没加载出来。
      </p>
    )
  }

  return (
    <>
      {(reds.length > 0 || guards.length > 0) && (
        <ul className="alerts">
          {reds.map((l, i) => (
            <li key={`r-${i}`} className="alert is-red">
              <b>{l.label}</b>
              {isShown(l.reason) && <span>{l.reason.data}</span>}
            </li>
          ))}
          <SafetyNotice items={guards} />
        </ul>
      )}

      {/* 2026-10-14：没有标题就**整个 header 不渲染**。此前它是空壳也照渲染，
          在候选轮里实测 0 个子元素、高度 0，却仍带着 .dish-hero 的
          `margin-bottom:30px` —— 页首到卡片之间凭白多出一条 30px 的死空白
          （候选轮的正文是从编号清单里解析出来的，本来就没有 Markdown 标题）。
          只在"标题真的存在"时才画，有真标题的那几轮因此逐像素不变。 */}
      {isShown(vm.heading) && (
        <header className="dish-hero">
          <h1 className="dish-name">{vm.heading.data}</h1>
          {/* 回声已升格成页首的 AskEcho 框（第 4 项），这里不再重复一遍原话：
              同一个要求在一屏里说两遍，第二遍只会像系统在自我解释。 */}
        </header>
      )}
      <Prose text={vm.text} />
    </>
  )
}

export function RunResult({
  run,
  onAgain,
  text,
  onText,
  onSend,
  blocked,
  onPickCandidate,
  onRequestImage,
  fam,
  week,
  fridge,
  readErr = null,
}: {
  run: RunState
  onAgain: () => void
  /** 结果页底部的书写面（甲方案）：结果出来之后仍然要能接着说下一句。
   *  输入状态由 app 层持有，本组件只透传——它不该多出一份自己的 text 副本，
   *  否则"结果页打的字"和"首屏打的字"会变成两个东西。 */
  text: string
  onText: (next: string) => void
  onSend: () => void
  blocked: boolean
  /** 候选卡「选这个方案」：把"第 N 个，菜名"直接发出去。
   *  必须走 send(text) 而不是 setText 后再 send——后者要等 React 状态落地，
   *  同一帧里调用会读到旧文本，发出去的是上一轮那句话。 */
  onPickCandidate: (text: string) => void
  /** 菜名右侧「配图 / 换一张图」（图片轮，无落点不发）。可见性不在这里判：
   *  与★/评分同一道闸（useResultActions.visible）在下面统一放行。 */
  onRequestImage?: (dishName: string, revision: boolean) => void
  /** 候选页右栏那三块的数据（第 F 项）。只有候选轮用得上，其余形态传 null
   *  ——普通散文轮与结构化轮不该因为这次改动多出一个右栏。 */
  fam?: { members: FamilyMemberRow[]; shared: string[] } | null
  week?: WeekView | null
  fridge?: { count: number; names: string } | null
  /** 首屏读失败的原因（useFirstScreen().readError）。候选页右栏与首屏读同一份数据，
   *  读不到时同样要说得出来（透传，不加工）。 */
  readErr?: string | null
}) {
  const form = resultForm(run.answer)
  const sources = run.answer?.sources ?? []
  const isStructured = form === 'structured'
  const searches = run.events.filter((e) => e.stage === 'searching').length
  const opening = isStructured ? cleanOpening(run.answer?.opening) : ''
  const chars = opening.length + (isStructured ? 0 : run.body.length)
  // 结构化路径的视图模型在这里建**一次**，往下传：组件不该各自再读一遍 answer。
  // 收藏恢复态传 null 用时——那一轮的真实用时没被收藏存下来，0 秒是编的。
  const vm = isStructured && run.answer
    ? buildResultVM(run.answer, run.body, run.request, run.restored ? null : run.elapsed, guardText)
    : null

  // ★/赞/踩：可见性、初始态回查、写动作都在 useResultActions 里（契约见其文件头）。
  const actions = useResultActions(run)

  /* 2026-10-15：书写面默认收起，点开才展开（理由见页尾那段注释）。
     这只是一个 UI 开关，**不是 text 的副本**——正文仍由 app 层持有并在下方透传，
     所以"结果页打的字"和"首屏打的字"依旧是同一份。 */
  const [sayOpen, setSayOpen] = useState(false)

  // 候选轮（两阶段点菜第一阶段）走的是**纯散文路径**：后端在推送前把结构化
  // candidates 丢掉了（chat_route.py:1561-1580），前端手上只有正文那串编号清单。
  // 所以这里从正文解析成卡片。判据是"≥2 道菜"——1 道菜不构成选择题，
  // 照卡片画反而把一句普通推荐拆成一张孤零零的卡。
  const cands = vm ? [] : parseCandidateList(run.body)
  const useCards = cands.length >= 2

  /* 页底三件事（真实过程 / 收藏赞踩 / 再做一顿）提成一个节点：
     2026-10-14（设计稿第二轮）候选轮把它们**移进左栏**、排在散文之后。
     原因是这一页的空白：右栏（档案 + 本周 + 冰箱）比左栏高一截，而左栏内容
     早早结束，于是页底那三件事被推到最后一行，中间留下几百像素空地。
     三件事本身一个字都没改——只是"渲染在哪里"由候选分支决定，
     其余轮次（结构化 / 散文）仍在原位置，排布逐字不变。 */
  const tail = (
    <>
      {/* 「真实过程」只对真实跑出来的那一轮开放。收藏里没有当时的用时与阶段
          事件，恢复态照画只会得到「用时 0 · 0 个事件」——那是编账目，改成实话。 */}
      {run.restored ? (
        <p className="process-note fv-restored-note">
          这条来自收藏：收藏只存了当时的答案，没有存用时和阶段事件，
          所以这里不列过程账目。想再跑一轮就点下面的「再做一顿」。
        </p>
      ) : (
        <details className="process">
          <summary>
            查看本轮的真实过程
            {run.events.length > 0 && `（${run.events.length} 个阶段事件）`}
          </summary>
          {/* 用时只写一次。原来是 `formatClock（formatSpoken）`，
              于是同一段时长被写成 `01:12（1 分 12 秒）`——两种格式说同一件事。 */}
          <p className="process-facts">
            用时 {formatSpoken(run.elapsed)}
            {searches > 0 && <> · 查证 {searches} 轮</>}
            {sources.length > 0 && <> · 依据 {sources.length} 条</>}
            {chars > 0 && <> · 正文 {chars} 字</>}
          </p>
          <ol className="process-list">
            <li>
              <span>-</span>
              <span>你的要求已经收到</span>
            </li>
            {run.events.map((e, i) => (
              <li key={i}>
                <span>{formatClock(e.at)}</span>
                <span>
                  {stageLabel(e.stage)}
                  {e.nth > 1 && ` 第 ${e.nth} 次`}
                </span>
              </li>
            ))}
            {run.firstTokenAt !== null && (
              <li>
                <span>{formatClock(run.firstTokenAt)}</span>
                <span>第一段正文到达</span>
              </li>
            )}
          </ol>
          {run.events.length > 0 && (
            <p className="process-note">
              这些是后端真正发过来的阶段事件。同一阶段的重复不是重新开始，
              那是它在反复查证。
            </p>
          )}
        </details>
      )}

      {/* 动作条（第 4 步下半场）：★收藏与评分的落点，也是新前端自己产生收藏的入口。
          收藏恢复态与失败/中断轮在这里整体不可见——可见性判据在 useResultActions。 */}
      {actions.visible && (
        <div className="rs-acts" role="group" aria-label="这顿的动作">
          {actions.lookupError ? (
            <>
              <p className="rs-state" data-tone="warn">
                {actions.lookupError}
              </p>
              <button className="fv-btn" type="button" onClick={actions.retry}>
                重试
              </button>
            </>
          ) : (
            <>
              <button
                className="fv-btn"
                type="button"
                data-on={actions.starred}
                disabled={actions.busy || actions.loading}
                onClick={() => void actions.toggleStar()}
              >
                <Icon name="star" />{actions.starred ? '已收藏' : '收藏'}
              </button>
              <button
                className="fv-btn"
                type="button"
                data-on={actions.feedback === 'up'}
                disabled={actions.busy || actions.loading}
                onClick={() => void actions.rate('up')}
              >
                赞
              </button>
              <button
                className="fv-btn"
                type="button"
                data-on={actions.feedback === 'down'}
                disabled={actions.busy || actions.loading}
                onClick={() => void actions.rate('down')}
              >
                踩
              </button>
              {actions.loading && <span className="rs-state">状态读取中…</span>}
              {actions.actionError && (
                <span className="rs-state" data-tone="warn">
                  {actions.actionError}
                </span>
              )}
            </>
          )}
        </div>
      )}

      <button className="again" type="button" onClick={onAgain}>
        再做一顿
      </button>
    </>
  )

  return (
    <section className={`result${isStructured ? '' : ' is-prose'}${useCards ? ' is-cand' : ''}`}>
      {/* 页眉只有右对齐的一个完成标记。
          品牌名「小膳管家 · 今晚这一顿」由外层外壳 .tn-head 提供，这里绝不能再来一遍——
          设计稿里那一行就是页头本身，不是结果区的一部分。
          用时/查证/依据这些系统账目也一律不在这里，它们落在页底的「真实过程」里：
          版面最贵的位置不该给内部计数，那是第二版被否决的原因之一。 */}
      {/* 页眉照预期图：左「返回今日方案」回首屏书写面（与页底「再做一顿」同一动作
          onAgain——顶部一个底部一个，不引入新行为），右侧完成态标记保留事实语义。 */}
      <header className="result-head">
        <button type="button" className="result-back" onClick={onAgain}>
          ‹ 返回今日方案
        </button>
        {/* 恢复态不是这一轮跑出来的「完成」，写「已完成」会让它冒充刚发生的事实。 */}
        <span className="result-tick">{run.restored ? '来自收藏' : '已完成'}</span>
      </header>

      {/* 需求回显框（第 4 项）。它**不挂在 vm 上、也不跟着结果形态走**：
          结构化与纯散文两条路径都要有，因为"你要求了什么"在两处同样成立。
          原话为空（收藏没存下它）时组件自己返回 null——绝不拿正文反推一句。 */}
      <AskEcho text={run.request} at={run.askedAt} />

      {vm ? (
        <StructuredResult
          vm={vm}
          body={run.body}
          askedAt={run.askedAt}
          /* 配图入口与★/评分同一道闸：restored（收藏详情只读）与无 origin（没有
             落点）的轮按钮根本不出现——图没地方落就不该给发图的机会。 */
          onRequestImage={actions.visible ? onRequestImage : undefined}
        />
      ) : useCards ? (
        /* 候选轮（第 F 项）：从"一列正文"变成"选择区 + 方案依据"，
           与预期图 cand-expect-v2.png 同构。右栏那三块复用首屏的组件——
           同一份数据在两个语境下的两种说法（variant='candidate' 换标题），
           不新建组件、也不再拉一次接口。
           2026-10-14（方向 B 第二轮）：B 稿是**横向铺开**的版心（左卡带 + 右信息栏），
           而 42em 的正文测度把整列收成 672、1055 的纸面里空掉 617px，三块参考信息
           只能挤成 213px 窄柱（实测最高 1100px，且「数据不足」被折成「数据不/足」）。
           故这一轮：① `is-cand` 改挂在**根** section 上（.result 那一层），才能放开
           `.result.is-prose > *` 的宽度上限，宽度规则见 styles/candidate.css 末节；
           ② <AskRail> 回到 .ask-main 之外、与候选卡并排成右栏——网格本体不动。 */
        <div className="ask-layout is-cand">
          <div className="ask-main">
            <CandidateCards
              list={cands}
              onPick={(c) => onPickCandidate(`第${c.index}个，${c.name}`)}
              onAsk={onText}
            />
            <ProseResult
              body={stripCandidateLines(run.body)}
              request={run.request}
              reds={[]}
              guards={[]}
            />
            {/* 页底三件事排在左栏散文之后（见上面 tail 的说明）：右栏比左栏高出一截，
                它们留在这儿才能把左栏填满，不再把一片空地留在页面中段。 */}
            <div className="rs-foot">{tail}</div>
          </div>
          {/* 参考信息回到**右栏**、与候选卡并排（与 B 稿同构）：.ask-layout 的网格
              本来就是 `minmax(0,1fr) var(--rail)`，两个语境共用它，所以这里只把
              <AskRail> 放回 .ask-main 之外，不再覆盖网格本身。
              cand 传 null：那个"待确认"块是首屏专属（它是档案里待用户拍板的事项），
              候选页要回答的是"为什么是这三个"，混进待确认事项只会跑题。 */}
          <AskRail fam={fam ?? null} week={week ?? null} cand={null} fridge={fridge ?? null} readErr={readErr} variant="candidate" />
        </div>
      ) : (
        <ProseResult
          body={run.body}
          request={run.request}
          reds={[]}
          guards={[]}
        />
      )}

      {/* 其余轮次（结构化 / 散文）页底三件事仍在原位置：顺序、间距、DOM 与改动前
          逐字一致——它们没有"右栏比左栏高"的问题，不该跟着候选轮一起挪。 */}
      {useCards && !vm ? null : (
      <>
      {/* 「真实过程」只对真实跑出来的那一轮开放。收藏里没有当时的用时与阶段
          事件，恢复态照画只会得到「用时 0 · 0 个事件」——那是编账目，改成实话。 */}
      {run.restored ? (
        <p className="process-note fv-restored-note">
          这条来自收藏：收藏只存了当时的答案，没有存用时和阶段事件，
          所以这里不列过程账目。想再跑一轮就点下面的「再做一顿」。
        </p>
      ) : (
        <details className="process">
          <summary>
            查看本轮的真实过程
            {run.events.length > 0 && `（${run.events.length} 个阶段事件）`}
          </summary>
          {/* 用时只写一次。原来是 `formatClock（formatSpoken）`，
              于是同一段时长被写成 `01:12（1 分 12 秒）`——两种格式说同一件事。 */}
          <p className="process-facts">
            用时 {formatSpoken(run.elapsed)}
            {searches > 0 && <> · 查证 {searches} 轮</>}
            {sources.length > 0 && <> · 依据 {sources.length} 条</>}
            {chars > 0 && <> · 正文 {chars} 字</>}
          </p>
          <ol className="process-list">
            <li>
              <span>-</span>
              <span>你的要求已经收到</span>
            </li>
            {run.events.map((e, i) => (
              <li key={i}>
                <span>{formatClock(e.at)}</span>
                <span>
                  {stageLabel(e.stage)}
                  {e.nth > 1 && ` 第 ${e.nth} 次`}
                </span>
              </li>
            ))}
            {run.firstTokenAt !== null && (
              <li>
                <span>{formatClock(run.firstTokenAt)}</span>
                <span>第一段正文到达</span>
              </li>
            )}
          </ol>
          {run.events.length > 0 && (
            <p className="process-note">
              这些是后端真正发过来的阶段事件。同一阶段的重复不是重新开始，
              那是它在反复查证。
            </p>
          )}
        </details>
      )}

      {/* 动作条（第 4 步下半场）：★收藏与评分的落点，也是新前端自己产生收藏的入口。
          收藏恢复态与失败/中断轮在这里整体不可见——可见性判据在 useResultActions。 */}
      {actions.visible && (
        <div className="rs-acts" role="group" aria-label="这顿的动作">
          {actions.lookupError ? (
            <>
              <p className="rs-state" data-tone="warn">
                {actions.lookupError}
              </p>
              <button className="fv-btn" type="button" onClick={actions.retry}>
                重试
              </button>
            </>
          ) : (
            <>
              <button
                className="fv-btn"
                type="button"
                data-on={actions.starred}
                disabled={actions.busy || actions.loading}
                onClick={() => void actions.toggleStar()}
              >
                <Icon name="star" />{actions.starred ? '已收藏' : '收藏'}
              </button>
              <button
                className="fv-btn"
                type="button"
                data-on={actions.feedback === 'up'}
                disabled={actions.busy || actions.loading}
                onClick={() => void actions.rate('up')}
              >
                赞
              </button>
              <button
                className="fv-btn"
                type="button"
                data-on={actions.feedback === 'down'}
                disabled={actions.busy || actions.loading}
                onClick={() => void actions.rate('down')}
              >
                踩
              </button>
              {actions.loading && <span className="rs-state">状态读取中…</span>}
              {actions.actionError && (
                <span className="rs-state" data-tone="warn">
                  {actions.actionError}
                </span>
              )}
            </>
          )}
        </div>
      )}

      <button className="again" type="button" onClick={onAgain}>
        再做一顿
      </button>
      </>
      )}

      {/* 「再做一顿」是重置，「继续」是接着说话——两件事都留着，因为它们不是一回事：
          重置会把这一轮的结果丢掉，而用户常常只是想在这道菜上改一句。
          这一页原来只给重置，于是拿到结果后无处说下一句——正文甚至写着
          「回一句第几个就行」，页面上却连一个输入框都没有（用户实测后提出）。

          2026-10-15：补上来的那个书写面**太重**。用户看完成品后判定它"肯定是不好的"。
          要改的不是"能不能说话"——入口必须一直在——而是默认态就把整块面板、
          两行隐私小字和语音/相机图标排一起铺在页尾，读起来像一张等着人填的表；
          它和上方「再做一顿」之间还隔着一大段空白，两块功能被拉成两截。

          改法：默认只留一行邀请 + 一枚暖色圆钮，点开才展开原来那个书写面。
          **没有退回"无处说下一句"**：从"摊开的表单"变成"一句邀请"而已。
          草稿非空时强制保持展开——否则刚说到一半点走再回来，字就"消失"了。 */}
      {sayOpen || text.trim().length > 0 ? (
        <div className="rs-say-open">
          <AskComposer
            variant="followup"
            text={text}
            onText={onText}
            onSend={onSend}
            running={false}
            blocked={blocked}
          />
          {text.trim().length === 0 && (
            <button className="rs-say-collapse" type="button" onClick={() => setSayOpen(false)}>
              收起
            </button>
          )}
        </div>
      ) : (
        <button
          className="rs-say"
          type="button"
          disabled={blocked}
          onClick={() => setSayOpen(true)}
        >
          <span className="rs-say-hint">想改这顿？说一句就行</span>
          <span className="rs-say-go" aria-hidden="true">→</span>
        </button>
      )}
    </section>
  )
}





















