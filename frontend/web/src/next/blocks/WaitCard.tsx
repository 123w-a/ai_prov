import type { RunState } from '../data/model.ts'
import { formatSpoken, isStale, sinceLastHeartbeat, stageLabel, stagePhrase, stageSentence } from '../data/model.ts'
import { familyFacts, useHousehold } from '../data/household.ts'
import { Panel } from '../ui/Panel.tsx'

/**
 * 等待界面 —— 「请求确认卡」。
 *
 * 这一版推翻了上一版的时间卷带。推翻的理由不是审美，是表达对象错了：
 *
 *   1. 卷带 + 心跳脉冲 = 地震仪 / 心电监护仪。这是一个吃饭的产品，而且档案里有一位孕妇，
 *      「琥珀色密集脉冲 + 时间轴」会把它污染成医疗焦虑。用户的原话就是「像心电图」。
 *   2. 把「后端还活着」放到视觉中心，用户感受到的不是透明，是「这东西很脆弱，得靠心跳维持」。
 *   3. 后端没有提供任何进度信息。上一版用「没有终点」来避免撒谎，方向是对的；
 *      但结论下早了——正确的下一步不是「把沉默画得更好看」，而是
 *      **不让这 7 分钟成为用户必须观看的事情**。
 *
 * 2026-09-22 第二轮改版：用户反馈「还在进行菜谱准备时的视觉真的不是很好」，
 * 由 kele_image_gen 出一张设计参考图（交付目录 设计稿-等待页-kelegen-20260922.png），
 * 照它改了三处结构，都跟"读起来像收据还是像详情页"有关：
 *   · 冰箱食材从一行逗号串（`鸡蛋 · 西兰花 · 面条 · 大蒜`）改成 2×2 的可扫读块。
 *     等待页要让人 2 秒确认"它知道我家有什么"，逗号串要逐字读，块可以一眼扫。
 *   · 家庭档案从 `dt/dd` 参数表改成一人一行。参数表是详情页的语言，
 *     而且原来那版会把「孕妇安全 · 增肌 · 不吃芹菜」压成一条，看不出谁是谁。
 *   · 状态行（最近收到 / 连接正常）从版面中段移到最底部并降到 12px。它是活动凭证，
 *     不是导航；放在中段会被当成"进度条标题"，那正是上一版被判的错。
 *   另去掉卡片投影——设计稿用 1px 发丝线分离卡片和背景，不靠光。
 *
 * 2026-09-23 第三轮改版（用户原话：「动态，而不是死的、看上去没有意思的项目」）：
 * 把"后端最近一次真实活动"从底部 12.5px 的凭证提升为版面中段的「当前工位」，
 * 并加一行真实痕迹 + 一道沉默闸门。**这是对下面 rule 3 的有理由推翻**，
 * 详细理由写在那段 JSX 上方，不要当成回归删掉。
 *
 * 所以这里现在是四件事：
 *   · 你的请求是什么（对象取自真实档案的 active 成员）
 *   · 后端此刻的工位是什么（真实阶段展开成一句人话；沉默超时后必须改口说没有新消息）
 *   · 真实到达过哪些工位（原样保留重复，重复本身就证明它不是流水线）
 *   · 连接是否还活着（静态状态点，不闪、不呼吸、不扩散）
 *
 * 硬规矩（来自对抗评审，全部可检验）：
 *   · 不显示累计时间。实测过「已等待 05:42」本身就会让用户推断"应该快了"。
 *     时间仍然被记录，但只出现在「处理详情」里。
 *   · 不把 查证 → 核对 → 整理 画成三步进度。后端会重复推这些阶段（循环 agent），不是流水线。
 *   · 食材只作为"已知输入"的证据，绝不占主位、不排序、不配图、不出现任何菜名——
 *     否则用户会以为菜单已经定了。
 *   · 动效只有三种，全部一次性：阶段改口 320ms 入场、状态点 160ms 变色、痕迹新项 320ms 入场。
 *     其余时间页面完全静止：不循环、不呼吸、不发光、不用心跳推动任何东西。
 */

/** 沉默多久算"比平时久"：实测最长一次连续 2 分 36 秒没有阶段事件。 */
const QUIET_HINT_MS = 150_000
/** 沉默多久从"刚刚"改为"结果尚未返回"。 */
const SETTLED_MS = 15_000

/**
 * 「当前工位」那一行从"正在"改为"上一步是"的阈值。
 *
 * 为什么要跟 QUIET_HINT_MS（150 秒）分开：那个 150 秒是给底部**连接行**用的
 * （「比平时久一些」是一个关于整轮的判断），而主行说的是"此刻在做什么"——
 * 用现在时断言一件两分半钟没有任何证据的事，跟本页"只展示系统确实知道的状态"的纪律冲突。
 *
 * 60 秒的取值理由：实测存在 106 秒的完全沉默区，所以 60 秒以上的空档是**常态**，
 * 这个改口在大多数轮次里都会真的发生（而不是一个永不触发的摆设）。
 * 它宁可早说，因为早说的内容是真的。
 */
const BENCH_STALE_MS = 60_000

/**
 * 「当前工位」那一行该说什么 —— 等待页唯一会由真实事件驱动变化的文案。
 *
 * 这里有一道**沉默闸门**，它是这一版最重要的诚实约束：
 * 实测最长一次连续 2 分 36 秒没有阶段事件。如果没有这道闸门，页面会拿最后那个阶段
 * 一直显示成"正在进行中"——而它在过去两分半里没有收到任何新消息，
 * 那就是在替后端编它没有说过的话。
 *
 * 所以沉默超过阈值后必须**改口**：不再说"正在核对…"，而是如实说没有新信息。
 * 最后那个阶段不会丢，它留在下面的痕迹行里（事实是"发生过"，不是"正在发生"）。
 *
 * quiet 同时被用来给节点换 key —— 改口那一刻也播一次入场，因为页面确实变了。
 */
function benchLine(run: RunState): { text: string; key: string; quiet: boolean } {
  if (run.orphaned) {
    return run.orphanCause === 'cancelled'
      ? { text: '这一轮已经取消', key: 'cancelled', quiet: false }
      : { text: '这一轮已经中断', key: 'left', quiet: false }
  }
  if (run.status === 'failed') {
    return { text: '这一轮没有成功', key: 'failed', quiet: false }
  }
  // 原文案是「已接单」——那是外卖/快递的语言，一个做饭的页面说"接单"是错位的。
  if (!run.currentStage) {
    return { text: '你的要求已经收到，正在开始', key: 'start', quiet: false }
  }
  const last = run.events.length > 0 ? run.events[run.events.length - 1].at : 0
  if (run.elapsed - last >= BENCH_STALE_MS) {
    // 保留事实、只改时态。初版这里是「后台仍在处理，暂时没有新的阶段信息。」——
    // 那一句确实诚实，但它把"刚才在做核对"这个**我们真的知道的事**一起丢掉了，
    // 用户于是拿不到任何信息。现在改成说清"上一步是什么"+"还没有新的"。
    return {
      text: `上一步是${stagePhrase(run.currentStage)}，还没有新的进展。`,
      key: 'stale',
      quiet: true,
    }
  }
  return { text: stageSentence(run.currentStage), key: run.currentStage, quiet: false }
}

function connLine(run: RunState): { text: string; tone: 'ok' | 'quiet' | 'warn' } {
  if (run.orphaned) {
    return {
      text:
        run.orphanCause === 'cancelled'
          ? '你取消了这一轮 · 连接已断开'
          : '已中断 · 页面离开时连接断开',
      tone: 'warn',
    }
  }
  if (run.status === 'failed') return { text: '这一轮没有成功', tone: 'warn' }
  if (isStale(run)) {
    return { text: `已 ${formatSpoken(sinceLastHeartbeat(run))} 没有心跳，可能已断`, tone: 'warn' }
  }
  const last = run.events.length > 0 ? run.events[run.events.length - 1].at : 0
  const quiet = run.elapsed - last
  if (quiet >= QUIET_HINT_MS) return { text: '连接正常 · 这次处理比平时久一些', tone: 'quiet' }
  if (quiet >= SETTLED_MS || run.elapsed >= SETTLED_MS) {
    return { text: '连接正常 · 结果尚未返回', tone: 'ok' }
  }
  return { text: '连接正常 · 菜单尚未确定', tone: 'ok' }
}

/* 原先这里有一份局部 type FamilyData 与 function familyFacts。
 * 已抽到 household.ts —— 起手页要用同一份推导，抄两份必然抄歪。 */

export function WaitCard({
  run,
  onCancel,
  onRestart,
}: {
  run: RunState
  onCancel?: () => void
  onRestart?: () => void
}) {
  // 家庭档案与冰箱的读取已经抽到 household.ts，与起手页共用同一份推导。
  // 仍然只读、不写；读失败就什么都不说。
  const { family, fridge, activeName } = useHousehold()

  const conn = connLine(run)
  const facts = familyFacts(family)
  const bench = benchLine(run)
  // 痕迹只保留最近 6 项。保留全部会让页面在长轮次里被历史堆满，
  // 而"更早发生过什么"在下面的「处理详情」里有完整流水（含第 n 次）。
  // 这里不显示总数，也不显示"共 n 项"——那会被读成进度。
  const trail = run.events.slice(-6)

  return (
    <section className="wait">
      <div className="wait-card">
        <h2 className="wait-req">
          正在为{activeName ? <b>{activeName}</b> : <b>你</b>}整理今晚的选择
        </h2>
        <p className="wait-sub">会结合家庭档案里的饮食约束，以及冰箱里现有的食材。</p>

        {/* ── 当前工位 + 真实痕迹 ──────────────────────────────────────
         *
         * 这一块是对本文件原第 406 行那段注释的**有理由的推翻**，理由必须写清楚，
         * 否则下一个人会以为它是回归：
         *
         *   被推翻的结论是「活动凭证放在卡片最底部、12.5px 灰字、不占版面」。
         *   当时的理由是：上一版把它放在版面中段、15px 加粗，读起来像进度条标题，
         *   是"像心电图"的入口。那个判断本身没错——**但它只否掉了"把它做成仪表"**，
         *   没有解决用户真正提的问题（2026-09-23 原话：「动态，而不是死的、看上去没有意思的项目」）。
         *   12.5px 灰字放在最底部，意味着"真实事件到达"这件事在页面上几乎不可见，
         *   页面在 100 秒沉默里和一张静态图没有区别——那正是"死"的来源。
         *
         * 区别在哪（这是允许提升它的关键，不是审美辩解）：
         *   · 进度条标题 = 「一共 N 步，现在第 k 步」。它需要总数，而**后端从不给总数**。
         *   · 当前工位   = 「正在 + 这个阶段的字面含义」。没有分母、没有比例、没有剩余。
         *   下面那行痕迹会**原样保留重复**（查证 · 核对 · 查证），
         *   这恰恰证明它不是流水线——真流水线不会退回上一步。
         *
         * 动效纪律：key 绑在阶段上，阶段一变就换新节点，320ms 入场**只播一次**，
         * 不循环、不呼吸、不发光。沉默期满改口时同样只播一次。
         * 唯一会持续变化的东西仍然只有底部的连接状态与心跳校准过的本地时钟。
         */}
        <div className="bench">
          <p className={`bench-now${bench.quiet ? ' is-quiet' : ''}`} key={bench.key}>
            {bench.text}
          </p>
          {trail.length > 0 && (
            <p className="bench-trail">
              <span className="bench-trail-label">已经过</span>
              {trail.map((e, i) => (
                // key 带 at：同一阶段第 2 次到达时也会重新入场，它确实是新到达的事件。
                <span className="bench-chip" key={`${e.at}-${i}`}>
                  {stageLabel(e.stage)}
                </span>
              ))}
            </p>
          )}
        </div>

        {(facts || fridge.length > 0) && (
          <div className="wait-facts">
            <Panel variant="outline">
            {fridge.length > 0 && (
              <section className="wait-fact">
                <h3 className="wait-fact-label">冰箱现有</h3>
                <ul className="wait-chips">
                  {fridge.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </section>
            )}
            {facts && (
              <section className="wait-fact">
                <h3 className="wait-fact-label">家庭档案</h3>
                {facts.members.map((line) => (
                  <p className="wait-fact-row" key={line}>
                    {line}
                  </p>
                ))}
                {facts.shared && <p className="wait-fact-shared">{facts.shared}</p>}
              </section>
            )}
            <p className="wait-caveat">以上只表示系统已经知道什么，菜单尚未确定。</p>
            </Panel>
          </div>
        )}

        {run.orphaned && (
          <p className="wait-orphan">
            {run.orphanCause === 'cancelled'
              ? '你取消了这一轮，所以它没有跑完。「处理详情」里是取消前真实收到过的记录。'
              : '页面被刷新或关掉时连接断了，这一轮没有跑完。「处理详情」里是中断前真实收到过的记录。'}
          </p>
        )}

        {run.error && <p className="wait-error">{run.error}</p>}

        {/* 顺序照设计稿：先给"详情"入口，再给按钮。读完了再决定要不要动作。 */}
        <div className="wait-actions">
          <details className="wait-detail">
            <summary>处理详情</summary>
            <p className="wait-detail-time">
              已用时 <span className="mono">{formatSpoken(run.elapsed)}</span>
              {run.heartbeats.length > 0 && <span> · 心跳 {run.heartbeats.length} 次</span>}
            </p>
            {run.events.length === 0 ? (
              <p className="wait-detail-none">至今没有收到任何阶段事件。</p>
            ) : (
              <ol className="wait-log">
                {run.events.map((e, i) => (
                  <li key={`${e.at}-${i}`}>
                    <span className="mono">{clockOf(e.at)}</span>
                    <span>正在{stageLabel(e.stage)}</span>
                    {e.nth > 1 && <span className="wait-log-nth">第 {e.nth} 次</span>}
                  </li>
                ))}
              </ol>
            )}
            <p className="wait-detail-note">
              后端不会逐步报告它做到哪一步。这里只列它真的发过来过的东西，所以条目少是事实，不是漏了。
            </p>
          </details>

          {run.status === 'running' && !run.orphaned && onCancel && (
            <button className="act act-quiet" type="button" onClick={onCancel}>
              取消本次请求
            </button>
          )}
          {run.orphaned && onRestart && (
            <button className="act act-quiet" type="button" onClick={onRestart}>
              重新开始
            </button>
          )}
        </div>

        {/* 连接状态留在最底部并保持 12.5px：它回答的是"连接还活着吗"，
            不是"做到哪一步了"。**这两件事从头到尾不许合流**——
            心跳只能证明连接，绝不能证明工作又前进了一格（lave 两席一致点名）。
            阶段那一行已经搬到上面的「当前工位」，「最近收到」不再是这里的职责。 */}
        <div className="wait-state">
          <p className={`wait-conn tone-${conn.tone}`}>
            <span className="wait-dot" />
            {conn.text}
          </p>
        </div>
      </div>

      {run.body && (
        <section className="wait-body-wrap">
          <h3 className="wait-body-title">
            {run.orphaned
              ? run.orphanCause === 'cancelled'
                ? '取消前已经写出的内容'
                : '中断前已经写出的内容'
              : '已经写出的内容'}
            <span className="wait-body-count">{run.body.length} 字</span>
          </h3>
          <div className="wait-body">{run.body}</div>
          <p className="wait-body-note">
            {run.orphaned
              ? '这是没有跑完的过程稿，不构成最终建议。'
              : '这只是过程稿，最终建议在整轮结束后才算完成。'}
          </p>
        </section>
      )}
    </section>
  )
}

/** 事件到达时刻换算成"开跑后第几分几秒"。卷带没了，但时刻本身是真实数据，不该丢。 */
function clockOf(atMs: number): string {
  const total = Math.floor(atMs / 1000)
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}
