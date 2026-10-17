import type { FamilyMemberRow } from '../data/firstScreen.ts'

/**
 * 「今天谁一起吃」——首屏的用餐人选择区（2026-10-15，用户裁决）。
 *
 * 为什么单独一块：一顿饭不一定全家一起吃。选完**确认**之后才解锁书写面，
 * 这一顿的归属就由此定下（后端记进 `members`，周报按它算每个人的分母）。
 *
 * 三个刻意的设计决定：
 *
 * 1 **「一个都不选」是合法确认，不是拦人的错误。** 它的语义是"这一顿不登记
 *   用餐人、也不套用任何成员约束"——这是一个有意义的回答（一个人凑合吃、
 *   或者根本没打算登记）。所以确认按钮**不因此禁用**；选了 0 人照样能确认。
 *   后端收到空数组就不写 `members` 键，历史里表现为"未归属"，诚实。
 *
 * 2 **未填约束的成员显示「未填写约束」，不显示"无约束"。** 这两件事不一样：
 *   前者是档案还没填（我们不知道），后者是真的没有忌口。把前者写成后者，
 *   就是拿"不知道"冒充"没有"——本仓不许补造的同一条纪律。判据是 `tags` 空。
 *
 * 3 **已选人数组由调用方持有，本组件不自己存 state。** 因为这份选择要跟着
 *   请求发出去（`chatStream` 的 `members`），状态得待在发起请求的那一层；
 *   组件各存一份就会出现"界面勾了三个、发出去两个"这种最难查的错。
 *
 * 本文件在 blocks/ 层：不许出现 button 之外的写控件、不许做存在性判断
 * （`check-layers` 的降级契约），所以 `members` 为空的情形由调用方决定怎么办。
 */
export function MealCrewPick({
  members,
  picked,
  onToggle,
  onSelectAll,
  confirmed,
  onConfirm,
  onEdit,
}: {
  members: FamilyMemberRow[]
  picked: string[]
  onToggle: (name: string) => void
  onSelectAll: () => void
  confirmed: boolean
  onConfirm: () => void
  onEdit: () => void
}) {
  /* 「全选」是不是已经全选了。**必须带上 members.length > 0 这一半**：
     家里一个人都没有时 picked 也是空的，光看 `picked.length === members.length`
     会得出 0 === 0 ⇒ 判成"已全选"，一个空名单上挂着一枚高亮的"全选"。
     可选链取长度那种写法在本层被 check-layers 的降级契约禁掉，所以写成显式比较。 */
  const allOn = members.length > 0 && picked.length === members.length
  return (
    <section className={`crew${confirmed ? ' is-confirmed' : ''}`} data-block="crew">
      <div className="crew-head">
        <h3 className="crew-title">今天谁一起吃</h3>
        <div className="crew-head-right">
          <span className="crew-count">
            {picked.length > 0 ? (
              <>
                已选中 <b>{picked.length}</b> 人
              </>
            ) : (
              <>还没选人</>
            )}
          </span>
          {/* 全选：一枚 toggle。未全选时点它选齐，已全选时点它清空——
              不写两个按钮（"全选 / 全不选"并排会让人先读一遍才知道该点哪个）。
              部分选中时它显示未选中态，点了补齐剩下的，这也符合"全选"的直觉。 */}
          <button
            className={`crew-all${allOn ? ' is-on' : ''}`}
            type="button"
            aria-pressed={allOn}
            disabled={confirmed}
            onClick={onSelectAll}
          >
            全选
          </button>
        </div>
      </div>

      <p className="crew-note">
        选好后确认，这一顿就按在座几位的健康情况来定，并记进周报。
      </p>

      <ul className="crew-list">
        {members.map((m) => {
          const on = picked.includes(m.name)
          return (
            <li key={m.id}>
              <button
                className={`crew-row${on ? ' is-on' : ''}`}
                type="button"
                aria-pressed={on}
                disabled={confirmed}
                onClick={() => onToggle(m.name)}
              >
                <span className="crew-box" aria-hidden="true" />
                <span className="crew-name">{m.name}</span>
                <span className="crew-tags">
                  {m.tags.length > 0 ? (
                    m.tags.map((t) => (
                      <span className="crew-tag" key={t}>
                        {t}
                      </span>
                    ))
                  ) : (
                    /* 空标签 = 档案里还没填，不是"没有忌口"。措辞不许含糊。 */
                    <span className="crew-tag is-blank">未填写约束</span>
                  )}
                </span>
              </button>
            </li>
          )
        })}
      </ul>

      {confirmed ? (
        <div className="crew-done">
          <p className="crew-done-t">这一顿就按这几位来定。</p>
          <button className="crew-edit" type="button" onClick={onEdit}>
            改一下
          </button>
        </div>
      ) : (
        <button className="crew-confirm" type="button" onClick={onConfirm}>
          确认这几个人吃
        </button>
      )}

      <p className="crew-foot">
        {picked.length > 0
          ? '想改随时点「改一下」。'
          : '一个都不选，表示这一顿不登记用餐人、也不套用任何成员约束。'}
      </p>
    </section>
  )
}


