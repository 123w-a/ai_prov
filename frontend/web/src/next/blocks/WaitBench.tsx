import type { RunState } from '../data/model.ts'
import type { benchLine } from '../data/waitLines.ts'
import { stageLabel } from '../data/model.ts'

/**
 * 等待页的「当前工位 + 已经过痕迹」（2026-10-01 从 WaitCard 拆出，行数红线）。
 *
 * 这一块是对其原第 406 行那段注释的**有理由的推翻**，理由必须写清楚，
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
 */
export function WaitBench({ run, bench }: { run: RunState; bench: ReturnType<typeof benchLine> }) {
  // 痕迹只保留最近 6 项。保留全部会让页面在长轮次里被历史堆满，
  // 而"更早发生过什么"在下面的「处理详情」里有完整流水（含第 n 次）。
  // 这里不显示总数，也不显示"共 n 项"——那会被读成进度。
  const trail = run.events.slice(-6)

  return (
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
  )
}
