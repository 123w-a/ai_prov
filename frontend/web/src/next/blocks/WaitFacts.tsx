import { useState } from 'react'
import { Icon } from '../ui/Icon.tsx'
import { ingredientImage } from '../data/ingredients.ts'
import type { familyFacts } from '../data/household.ts'

type Facts = ReturnType<typeof familyFacts>

/**
 * 等待页「它已知的输入」（2026-10-12 照 .tmp-img/wait-redesign-r1.png 重排）。
 *
 * 稿把这一屏拆成左右两半、中间一条竖发丝线：
 *   左半 四张**图在上、名在下**的食材瓦片（冰箱现有几样，标题里就报数）
 *   右半 一人一块的成员瓦片 + 收束出的一块「共同忌口」红瓦片
 *
 * 与上一版的三处结构差异，都是有理由的推翻，不是顺手改：
 *   · 食材从"图在名字左边的一条横链"改成"图在上、名在下的一列瓦片"。稿是竖排；
 *     竖排也让四张图落进同一条基线上，2 秒扫读"它知道我家有什么"靠的是图，不是字。
 *   · 成员从"一人一行的文字"改成瓦片。文字行读起来是**参数表**，瓦片读起来是**人**——
 *     这是这一屏的用途（确认"它知道家里有谁、谁忌什么"），所以取后者。
 *   · 删掉底部的 caveat（「以上只表示系统已经知道什么，菜单尚未确定。」）与
 *     「共同忌口」摘要行：稿里没有。共同忌口已经升格成第三块瓦片（见下），
 *     而"菜单未定"由大标题的「正在…整理」直接承担，不再需要一句话兜底。
 *
 * 纪律（沿用旧版文件头第 3 条，一条没松）：食材只作为"已知输入"的证据，
 * 绝不占主位、不排序、不出现任何菜名——否则用户会以为菜单已经定了。
 * 图只出现在**已登记**的食材上（data/ingredients.ts 的注册表判定，未登记只出名字），
 * 且永远画的是**食材本身**（鸡蛋就是一颗鸡蛋），不是任何一道菜。
 */
export function WaitFacts({
  facts,
  fridge,
  tiles,
  shared,
}: {
  facts: Facts | null
  fridge: string[]
  tiles: { name: string; tag: string | null }[]
  shared: string | null
}) {
  if (!facts && fridge.length === 0) return null

  return (
    <section className="wait-facts">
      <h3 className="wait-facts-title">它已知的输入</h3>
      <p className="wait-facts-sub">已获取你家的食材、家庭成员与忌口信息</p>

      <div className="wait-cols">
        {fridge.length > 0 && (
          <section className="wait-col">
            {/* 数在标题里：稿写的是「（4样）」，所以这里报的必须是**读到的条数**，
                不是"大约几样"、更不是后端会给的总数（后端从不给总数）。 */}
            <h4 className="wait-col-label">
              <Icon name="fridge" />
              冰箱现有食材（{fridge.length}样）
            </h4>
            <ul className="wait-chips">
              {fridge.map((item) => (
                <FridgeChip key={item} item={item} />
              ))}
            </ul>
          </section>
        )}

        {(tiles.length > 0 || shared) && (
          <section className="wait-col">
            <h4 className="wait-col-label">
              <Icon name="people" />
              家庭成员与忌口
            </h4>
            <ul className="wait-members">
              {tiles.map((t) => (
                /* 轮廓按**标签里有没有"孕"字**选：稿上那位的腹部是外鼓的。
                   这不是按人名硬编码（换个人、换个名字，判据不变），
                   也不是医学判断——只是把档案里已有的那两个字画得能被一眼认出来。 */
                <li className="wait-member" key={t.name}>
                  <Icon name={t.tag && t.tag.includes('孕') ? 'pe' : 'person'} />
                  <span className="wait-member-name">{t.name}</span>
                  {t.tag && <span className="wait-member-tag">{t.tag}</span>}
                </li>
              ))}
              {shared && (
                /* 「共同忌口」是冰箱 ∩ 档案求出来的**交集**（data/household.ts 的
                   sharedDislike，≥2 人才算），不是家庭档案的一个属性。稿把它画成
                   与成员并列的一块瓦片——两路依据收束成一条约束，位置是对的。 */
                <li className="wait-member is-shared">
                  <Icon name="ban" />
                  <span className="wait-member-name">共同忌口</span>
                  <span className="wait-member-tag">{shared}</span>
                </li>
              )}
            </ul>
          </section>
        )}
      </div>
    </section>
  )
}

/** 冰箱的一个瓦片：**命中注册表才配图**，未命中只出名字。
 *
 *  三处刻意的克制，缺一条就会出现"闪一下破图"：
 *   1. 发不发请求**由注册表决定**——ingredientImage 返回 null 时压根没有 <img>，
 *      所以未登记的食材不可能有"先请求、再 404、再消失"那一帧；
 *   2. onError 是最后一道（打包漏文件 / 磁盘改名 / 代理截断）：图真的加载失败，
 *      就把 <img> 从树里摘掉、只留名字——用户看到的是"少一张图"，不是碎图标；
 *   3. 名字永远是纯文本，不因缺图退化成占位符、问号或空框。
 *  失败态用 state 记：它只在**真的 error 之后**变 true，不参与"预判图片是否存在"。
 *  第 2、3 条在竖排下更要紧：四张图并排在同一条基线上，任何一张塌成破图都会
 *  把那一行读成"这里坏了三张"。 */
function FridgeChip({ item }: { item: string }) {
  const [broken, setBroken] = useState(false)
  const src = broken ? null : ingredientImage(item)
  return (
    <li className="wait-chip" data-img={src !== null ? '1' : undefined}>
      {src !== null && (
        <img
          className="wait-chip-img"
          src={src}
          /* 名字就在图下面，alt 再念一遍只是噪音；这张图是补充，不是唯一信息源。 */
          alt=""
          width={60}
          height={60}
          onError={() => setBroken(true)}
        />
      )}
      <span className="wait-chip-name">{item}</span>
    </li>
  )
}
