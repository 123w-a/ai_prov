import type { MembersVM } from '../data/viewModel.ts'

/**
 * 家人两列。
 *
 * 只在真有数据时才渲染（实测只 1/23）。有数据时它就该是页面上最该被看见的一块：
 * 这个产品存在的理由就是「一张菜谱同时照顾孕期和增肌两个人」。
 * 但它**不套盒子**——两列之间只用一条发丝竖线。盒子一旦出现在这里，
 * 就变成"给卡片加装饰"，而对抗席明令不许卡片靠装饰显得更高级。
 *
 * ── 两个实测出来的渲染规则（都是看真实数据才发现的）──────────────────
 *
 * 1. **一人一行，绝不把 member_adjustments 和 dish_matrix 都印出来。**
 *    实测同一条答案里这两处讲的是同一件事：
 *      member_adjustments = "我：「糖色香菇红烧肉（全家可吃版）」主食减半、蔬菜加量、不额外淋油；保留优质蛋白质"
 *      dish_matrix[我]     = { verdict:"需调整", reason:"主食减半、蔬菜加量、不额外淋油；保留优质蛋白质" }
 *    两处都渲染，那一列就会把同一句话印两遍。
 *    现在的合成方式：**verdict 当引子（设计稿要的两字引子就是它），正文取调整文案、
 *    取不到才退回 matrix.reason**。调整文案开头的「菜名」前缀要剥掉。
 *
 * 2. **单菜答案里不重复印菜名。**
 *    实测有 dish_matrix 的那条答案，矩阵内只有 1 个不同菜名，而它是整页的标题——
 *    在每人每行里再印一遍是纯噪声。规则：矩阵里出现超过 1 个菜名时才印。
 */
export function Members({ vm }: { vm: MembersVM }) {
  const { order, lines, orphans } = vm

  return (
    <section className="members">
      <div className="members-grid">
        {order.map((name) => (
          <div className="member-col" key={name}>
            <h3 className="member-name">{name}</h3>
            {lines[name].map((l) => (
              <p className="member-line" key={l.key}>
                {l.verdict && <b className="member-verdict">{l.verdict}</b>}
                {l.dish && <span className="member-dish">{l.dish}</span>}
                {l.body && <span className="member-reason">{l.body}</span>}
              </p>
            ))}
          </div>
        ))}
        {orphans.length > 0 && (
          <div className="member-col">
            <h3 className="member-name">其他</h3>
            {orphans.map((t, i) => (
              <p className="member-line" key={i}>
                <span className="member-reason">{t}</span>
              </p>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
