import type { RunState } from '../data/model.ts'
import { familyFacts, useHousehold } from '../data/household.ts'
import { benchLine, connLine } from '../data/waitLines.ts'
import { WaitBench } from './WaitBench.tsx'
import { WaitFacts } from './WaitFacts.tsx'
import { WaitDetail } from './WaitDetail.tsx'

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
 *   · 冰箱食材从一行逗号串改成 2×2 的可扫读块（等待页要让人 2 秒确认"它知道我家有什么"）。
 *   · 家庭档案从参数表改成一人一行（参数表是详情页的语言，而且会把两个人压成一条）。
 *   · 状态行（最近收到 / 连接正常）从版面中段移到最底部并降到 12px（它是活动凭证，不是导航）。
 *   另去掉卡片投影——设计稿用 1px 发丝线分离卡片和背景，不靠光。
 *
 * 2026-09-23 第三轮改版（用户原话：「动态，而不是死的、看上去没有意思的项目」）：
 * 把"后端最近一次真实活动"从底部 12.5px 的凭证提升为版面中段的「当前工位」，
 * 并加一行真实痕迹 + 一道沉默闸门。**这是对下面 rule 3 的有理由推翻**，
 * 详细理由在 WaitBench.tsx 的文件头，不要当成回归删掉。
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
 *
 * 2026-10-01 拆分：319 行越过 blocks 层 150 行红线，纯文案规则下沉 data/waitLines.ts，
 * 当前工位 / 证据面板 / 处理详情三块拆成同目录子组件，本文件只留头注释与组装。
 * 渲染不变的判据见夹具台 wait case 的结构与几何基线（拆分前后逐节点比对）。
 */

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
  // live:false = 本轮快照——这一轮的档案在启动时定死，中途在抽屉里换人
  // 不该让正在等待的这一页改口（lave 契约：进行中的轮绑定启动时快照）。
  const { family, fridge, activeName } = useHousehold({ live: false })

  const conn = connLine(run)
  const facts = familyFacts(family)
  const bench = benchLine(run)

  return (
    <section className="wait">
      <div className="wait-card">
        <h2 className="wait-req">
          正在为{activeName ? <b>{activeName}</b> : <b>你</b>}整理今晚的选择
        </h2>
        <p className="wait-sub">会结合家庭档案里的饮食约束，以及冰箱里现有的食材。</p>

        {/* 当前工位 + 真实痕迹：推翻理由见 WaitBench.tsx 文件头（原 406 行注释）。 */}
        <WaitBench run={run} bench={bench} />

        {/* 已知输入证据（冰箱 + 档案）：纪律见 WaitFacts.tsx 文件头。 */}
        <WaitFacts facts={facts} fridge={fridge} />

        {run.orphaned && (
          <p className="wait-orphan">
            {run.orphanCause === 'cancelled'
              ? '你取消了这一轮，所以它没有跑完。「处理详情」里是取消前真实收到过的记录。'
              : '页面被刷新或关掉时连接断了，这一轮没有跑完。「处理详情」里是中断前真实收到过的记录。'}
          </p>
        )}

        {run.error && <p className="wait-error">{run.error}</p>}

        {/* 详情入口 + 动作按钮（顺序照设计稿：先读再决定）。 */}
        <WaitDetail run={run} onCancel={onCancel} onRestart={onRestart} />

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
