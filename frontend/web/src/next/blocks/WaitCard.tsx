import type { RunState } from '../data/model.ts'
import { familyFacts, familyTiles, sharedDislike, useHousehold } from '../data/household.ts'
import { benchLine, connLine } from '../data/waitLines.ts'
import { WaitFacts } from './WaitFacts.tsx'
import { WaitDetail } from './WaitDetail.tsx'
import { WaitStream } from './WaitStream.tsx'
import { WaitAnswer } from './WaitAnswer.tsx'

/**
 * 等待界面（2026-10-12 第四轮改版：照 .tmp-img/wait-redesign-r1.png 重排）。
 *
 * 这一版与前三版的关系，先说清楚哪一条被有意推翻、哪一条是**首次**落进这一页：
 *
 *   · 首次：页内页眉（印章 + 小膳管家 | 家庭膳食规划 + 右侧那句）。它是**本页自己**的
 *     页眉，随 WaitCard 一起挂载/卸载，只在等待态存在。骨架明令禁止的是"跨页常驻的
 *     顶部横向导航"——所以它绝不能搬进 Shell 或 .tn-head，那样就变回被禁的那条。
 *   · 首次：标题下那条**渐隐横线**。它是静止的装饰（linear-gradient 画的一条线），
 *     不是进度条：宽度写死、不随任何计数变化、无动画。任何让它"长出来"的做法
 *     都会立刻读成进度（用户在 2026-09-22 明确判过"动效=进度"）。
 *   · 有理由推翻 2026-09-23 的「真实痕迹」行：稿里没有它，而痕迹的**完整记录**本来
 *     就在「处理详情」里（时刻 + 阶段 + 第几次，一条不少）。摘掉的只是版面上那 6 条
 *     摘要，不是记录本身。WaitBench.tsx 随之删除。
 *   · 有理由删掉卡片副标题、caveat 与「共同忌口」摘要行：稿把这三处的内容各自换了
 *     载体——副标题并进卡片标题下的「已获取你家的食材、家庭成员与忌口信息」，共同忌口
 *     升格成第三块瓦片，caveat 那句"菜单尚未确定"由大标题的「正在…整理」承担。
 *   · 不动：不显示累计时间、不画三步进度、食材只作已知输入、动效一次性。
 *
 * 仍守着的硬规矩（对抗评审留下的，全部可检验）：
 *   · 阶段那一行只说后端真发过的阶段展开的人话（推导在 data/waitLines.ts），
 *     沉默超时后必须改口说"没有新消息"——绝不为了填满版面而编一个步骤。
 *   · 连接状态只在**说不正常**的时候出现。稿画的是正常态，而"连接正常"本来就是
 *     默认预期；断线或沉默超时才是用户必须知道的事，那时它出现在最底部。
 */

/** 本页页眉。作成组件只为让 WaitCard 的组装段读起来是"版面的几块"而不是一堆 span。 */
function WaitHead() {
  return (
    <header className="wait-head">
      <span className="wait-head-l">
        {/* 印章图形复用 tokens 的品牌 mark（碗 + 叶），用 mask 取形状、填 seal-ink：
            稿里那枚章与此前的品牌图形是同一枚，不该再画第二版。 */}
        <span className="wait-seal" aria-hidden="true" />
        <span className="wait-brand">小膳管家</span>
        <span className="wait-head-div" aria-hidden="true" />
        <span className="wait-head-note">家庭膳食规划</span>
      </span>
      <span className="wait-head-tag">用家里的食材 · 做更合适的饭</span>
      {/* 稿右上角那幅装饰（茶碗+热气+远山+叶+朱砂印）：纯装饰，且是绝对定位浮层，
          不占位不推挤，页眉与后续每块几何零位移。落点见 wait.css 的 .wait-decor。 */}
      <img
        className="wait-decor"
        src="/assets/hero/wait-decor.png"
        alt=""
        aria-hidden="true"
        draggable={false}
      />
    </header>
  )
}

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
  const tiles = familyTiles(family)
  const shared = sharedDislike(family)
  const bench = benchLine(run)

  return (
    <section className="wait">
      <WaitHead />

      <h2 className="wait-req">
        正在为{activeName ? <b>{activeName}</b> : <b>你</b>}整理今天的选择
      </h2>

      {/* 当前工位。key 挂在 bench.key 上：阶段改口时这一行整条重挂，320ms 入场
          （一次性），阶段没变则 DOM 不动、页面完全静止。 */}
      <p className={`wait-step${bench.quiet ? ' is-quiet' : ''}`} key={bench.key}>
        <span className="wait-step-dot" aria-hidden="true" />
        <span className="wait-step-text">{bench.text}</span>
        <span className="wait-step-line" aria-hidden="true" />
      </p>

      {/* 已知输入证据（冰箱 + 档案）：纪律见 WaitFacts.tsx 文件头。 */}
      <WaitFacts facts={facts} fridge={fridge} tiles={tiles} shared={shared} />

      {run.orphaned && (
        <p className="wait-orphan">
          {run.orphanCause === 'cancelled'
            ? '你取消了这一轮，所以它没有跑完。「处理详情」里是取消前真实收到过的记录。'
            : '页面被刷新或关掉时连接断了，这一轮没有跑完。「处理详情」里是中断前真实收到过的记录。'}
        </p>
      )}

      {run.error && <p className="wait-error">{run.error}</p>}

      {/* 顺序照稿：记录 → 过程稿 → 出口。活卡片在过程稿之后（详见 WaitAnswer 头注释）。 */}
      <WaitDetail run={run} />
      <WaitStream run={run} />
      <WaitAnswer run={run} />

      {/* 出口留在右下角，且是一条**低调文字**而不是整宽按钮：稿这一处是"想清楚
          再点"的位置，不是主行动（主行动在结果页）。 */}
      <div className="wait-actions">
        {run.orphaned ? (
          onRestart && (
            <button type="button" className="wait-exit" onClick={onRestart}>
              重新开始
            </button>
          )
        ) : (
          run.status === 'running' &&
          onCancel && (
            <button type="button" className="wait-exit" onClick={onCancel}>
              取消
            </button>
          )
        )}
      </div>

      {/* 连接状态只在说不正常时出现（理由见文件头）：正常态这一页与稿完全同构，
          断线/沉默时才多出这一行，且它只回答"连接还活着吗"，绝不回答"做到哪一步"。 */}
      {conn.tone !== 'ok' && (
        <div className="wait-state">
          <p className={`wait-conn tone-${conn.tone}`}>
            <span className="wait-dot" />
            {conn.text}
          </p>
        </div>
      )}
    </section>
  )
}
