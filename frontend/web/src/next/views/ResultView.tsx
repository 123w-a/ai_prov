import type { RunState } from '../data/model.ts'
import { formatClock, formatSpoken, resultForm, stageLabel } from '../data/model.ts'
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
import { ChapterRail } from '../blocks/ChapterRail.tsx'
import { ChefTip } from '../blocks/ChefTip.tsx'
import { Steps } from '../blocks/Steps.tsx'
import { Sources } from '../blocks/Sources.tsx'
import { ExtraRecipes } from '../blocks/ExtraRecipes.tsx'
import { Fold } from '../ui/Fold.tsx'
import { DishHero } from '../blocks/DishHero.tsx'
import { Members } from '../blocks/Members.tsx'
import { buildProseVM, buildResultVM, isShown } from '../data/viewModel.ts'
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

function StructuredResult({ vm, body }: { vm: ResultVM; body: string }) {
  // 这一版只做一件事：把原来散在这里的判断换成读 vm。判断本身一个字没改，
  // 全部照抄进了 viewModel.ts（含"空右栏整块不渲染"这种踩过坑才补上的外层条件）。
  const sources = vm.sources

  return (
    <div className="result-body">
      {/* 页内章节轨道（第 2 项）。真实章节少于两个时它自己返回 null，
          所以这里不做条件渲染——判据属于那个组件。 */}
      <ChapterRail />

      {isShown(vm.rail) && <SideRail vm={vm} sources={sources} />}

      <div className="result-main">
      {/* ── 首屏 ────────────────────────────────────────────────────── */}

      {/* 下面四个 id 是章节轨道的锚点。包一层不带样式的 div 只为挂 id：
          本文件里不存在 `.result-main > X` 这类直接子选择器（已核），
          子元素各自的 margin 也不受影响，所以布局一个像素没动。 */}
      <div id="sec-dish"><DishHero vm={vm} /></div>

      {/* 红/黄判据已移到常驻右栏（见上方 .result-rail）。它们**没有降级**：
          仍然在首屏、仍然绝不默认折叠，只是从"读到这里才看见"变成"一直看得见"。
          这正是设计稿第一版解决的问题。 */}

      {isShown(vm.chefTip) && (
        <div id="sec-focus">
          <ChefTip tip={vm.chefTip.data} />
        </div>
      )}

      {/* 家人两列：只在真有数据时出现，不套盒子。 */}
      {isShown(vm.members) && <Members vm={vm.members.data} />}

      {/* ── 滚动 ────────────────────────────────────────────────────── */}

      {/* 营养判据已移到常驻右栏。 */}

      {isShown(vm.steps) && (
        <div id="sec-steps">
          <Steps steps={vm.steps.data} seasonings={vm.seasonings} />
        </div>
      )}

      {/* ── 折叠 ────────────────────────────────────────────────────── */}

      <div id="sec-fold">
        <OpeningFold opening={isShown(vm.opening) ? vm.opening.data : ''} body={body} />
      </div>

      {isShown(vm.rest) && <ExtraRecipes recipes={vm.rest.data} />}

      {sources.length > 0 && <Sources sources={sources} />}
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

      <header className="dish-hero">
        {isShown(vm.heading) && <h1 className="dish-name">{vm.heading.data}</h1>}
        {isShown(vm.echo) && <p className="dish-echo">按你「{vm.echo.data}」来配</p>}
      </header>

      <Prose text={vm.text} />
    </>
  )
}

export function RunResult({ run, onAgain }: { run: RunState; onAgain: () => void }) {
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

  return (
    <section className={`result${isStructured ? '' : ' is-prose'}`}>
      {/* 页眉只有右对齐的一个完成标记。
          品牌名「小膳管家 · 今晚这一顿」由外层外壳 .tn-head 提供，这里绝不能再来一遍——
          设计稿里那一行就是页头本身，不是结果区的一部分。
          用时/查证/依据这些系统账目也一律不在这里，它们落在页底的「真实过程」里：
          版面最贵的位置不该给内部计数，那是第二版被否决的原因之一。 */}
      <header className="result-head">
        {/* 恢复态不是这一轮跑出来的「完成」，写「已完成」会让它冒充刚发生的事实。 */}
        <span className="result-tick">{run.restored ? '来自收藏' : '已完成'}</span>
      </header>

      {vm ? (
        <StructuredResult vm={vm} body={run.body} />
      ) : (
        <ProseResult
          body={run.body}
          request={run.request}
          reds={[]}
          guards={[]}
        />
      )}

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
                {actions.starred ? '✓ 已收藏' : '★ 收藏'}
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
    </section>
  )
}















