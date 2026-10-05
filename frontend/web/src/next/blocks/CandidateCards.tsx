import type { Candidate } from '../data/candidates.ts'

/**
 * 候选卡（两阶段点菜·第一阶段）。
 *
 * ── 它凭什么只有这些内容 ────────────────────────────────────────────
 * 预期图 .tmp-img/cand-expect-v2.png 里每张卡有四行属性（烹饪时间 / 所需厨具 /
 * 关键特点 / 更适合谁），但后端**一行都没有**：候选轮的 payload 只有一个菜名
 * 字符串数组（"candidates": names），而它还被 chat_route 在推送前丢掉了
 * （见 data/candidates.ts 文件头）。所以这里**只画真的有的东西**——
 * 序号、菜名、一个选择动作；属性位不摆占位空行、不写"待补充"，更不编。
 * 卡脚那行小字说的是实话：这一轮后端本来就只承诺"回复序号，我再给完整做法"。
 *
 * ── 点击回什么 ──────────────────────────────────────────────────────
 * 回「第 N 个，菜名」：位数是后端自己解析出来的那个序号（同一套正则，
 * 见 data/candidates.ts），名字是给用户看的人话。两者都带上是因为后端
 * 第一阶段同时认序号与菜名，只带一个都会让某条路径落空。
 */
export function CandidateCards({
  list,
  onPick,
  onAsk,
}: {
  list: Candidate[]
  onPick: (c: Candidate) => void
  /** 「以上都不想吃」的换口径快捷句：只把话放进输入框，不替用户发出去——
   *  这几句是需要他自己改口味的半成品（"换清淡一点"到底多清淡），
   *  直接发出去等于替他决定了没说的那一半。 */
  onAsk: (text: string) => void
}) {
  const asks = ['换个口味', '换些食材', '做法简单点', '直接告诉我吃什么']
  return (
    <div className="cand">
      <ul className="cand-list">
        {list.map((c) => (
          <li className="cand-card" key={c.index}>
            <span className="cand-no">{c.index}</span>
            <b className="cand-name">{c.name}</b>
            <button
              className="cand-pick"
              type="button"
              onClick={() => onPick(c)}
            >
              选这个方案
            </button>
          </li>
        ))}
      </ul>
      <div className="cand-more">
        <span className="cand-more-label">以上都不想吃？</span>
        <ul className="cand-asks">
          {asks.map((a) => (
            <li key={a}>
              <button className="cand-chip" type="button" onClick={() => onAsk(a)}>
                {a}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
