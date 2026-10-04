import { useEffect, useState } from 'react'

/**
 * 结果页的页内章节轨道（2026-10-04 · 第 2 项）。
 *
 * ═══ 为什么不是「把顶部导航搬到左侧」═══════════════════════════════════════
 *
 * 预期图里那条左轨道承载的是**它产品的六步流程**（理念 → 需求 → 食谱结果 →
 * 采购清单 → 烹饪计划 → 营养总结）。本产品没有这个流程：只有四个房间，
 * 而「今晚这一顿」到结果是**一步完成**的。照搬会得到一条点了没反应、
 * 也无法向用户解释的装饰轨道——那正是 lave 点名的头号翻车方式。
 * lave 的裁决与此一致：**轨道承载什么，决定它是什么。**
 *
 * 所以这条轨道做的是**结果页内部的章节导航**：菜品 / 重点 / 做法 / 完整说明。
 * 四个节点各自对应正文里**真实存在**的一个块，全部是可聚焦的真实锚点链接
 * （href 指向 .result-main 里那四个 id），当前章节用 aria-current="location"
 * 标注、由 IntersectionObserver 维护。
 *
 * 分工：左轨道承担**语义导航**，右栏那条标尺承担**测量语汇**。两者不同职责，
 * 不互相抢注意力。
 *
 * ═══ 为什么整条轨道可能不渲染 ═════════════════════════════════════════════
 *
 * 真实数据里 14/35 是**纯散文**形态，那种结果页根本没有 .dish-top / .steps-block。
 * 于是：
 *   ① 缺哪个块就少哪个节点（不是渲染一个点了没用的死节点）；
 *   ② 真实章节**少于两个**时整条轨道返回 null——只有一个节点的「导航」
 *      是装饰，不是导航。这条是硬门槛，不是优化。
 *
 * ═══ 高亮为什么不用 IntersectionObserver（实测踩过）═══════════════════════
 *
 * 第一版用 IO + 一条窄触发带。滚到「重点」时高亮却跳到「做法」：IO 的回调
 * **只包含状态发生变化的元素**，而 .focus 只有 149px 高、.steps-block 紧随其后，
 * 两块同时落在带内时，谁的变化事件后到谁就赢——这是竞态，不是参数没调好。
 *
 * 改成判定线（视口 22% 处）+ 几何比较：**取最后一个已经越过判定线的章节**。
 * 语义确定、无竞态；这也不是「把滚动位置写死」——判据是元素实时的
 * getBoundingClientRect，章节高度变了它会自己跟着走。
 */

type Chapter = { label: string; id: string }

const CH: Chapter[] = [
  { label: '菜品', id: 'sec-dish' },
  { label: '重点', id: 'sec-focus' },
  { label: '做法', id: 'sec-steps' },
  { label: '完整说明', id: 'sec-fold' },
]

/** 判定线：视口高度的这个比例。越过它的最后一个章节就是当前章节。
 *  取 10%（实测值，不是拍脑袋）：取 22% 时，滚到只有 149px 高的「重点」后，
 *  紧跟其后的「做法」顶部也已经越线（只差约 179px），于是高亮跳到了下一节。 */
const LINE_RATIO = 0.1

export function ChapterRail() {
  const [active, setActive] = useState(0)
  // getElementById 返回的是 HTMLElement（不是 Element），类型要跟着它走，
  // 否则下面的 filter 谓词与 indexOf 都会因为宽窄不匹配而挂。
  const [found, setFound] = useState<Array<HTMLElement | null>>([])

  useEffect(() => {
    // 空元素不算章节：那几个 id 挂在包裹 div 上，而包裹的内容可能是
    // 「有数据才渲染」的（如 chef_tip 缺失时 #sec-focus 会是空的）。
    const els = CH.map((c) => {
      const el = document.getElementById(c.id)
      return el && (el.textContent ?? '').trim() ? el : null
    })
    setFound(els)

    if (els.filter(Boolean).length < 2) return

    // 最后一个真实章节的下标（滚到页面底端时用它）。
    const lastIdx = els.reduce((acc, e, i) => (e ? i : acc), 0)

    let raf = 0
    const pick = () => {
      raf = 0
      const line = window.innerHeight * LINE_RATIO
      let best = 0
      for (let i = 0; i < els.length; i += 1) {
        const el = els[i]
        if (el && el.getBoundingClientRect().top <= line) best = i
      }
      // 到底时最后一节可能**永远**越不过判定线（页面已经滚不动了），
      // 那样高亮会一直留在上一节、与眼前的标题不符。直接判它。
      const doc = document.documentElement
      if (window.innerHeight + window.scrollY >= doc.scrollHeight - 4) best = lastIdx
      setActive(best)
    }
    const onScroll = () => {
      if (raf) return // rAF 节流：滚动事件远密于渲染帧
      raf = window.requestAnimationFrame(pick)
    }
    pick()
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll, { passive: true })

    return () => {
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
      if (raf) window.cancelAnimationFrame(raf)
    }
  }, [])

  if (found.filter(Boolean).length < 2) return null

  return (
    <nav className="chap-rail" aria-label="本页章节">
      <ol>
        {CH.map((c, i) =>
          found[i] ? (
            <li key={c.id}>
              <a
                href={`#${c.id}`}
                aria-current={i === active ? 'location' : undefined}
                onClick={(ev) => {
                  const t = found[i]
                  if (!t) return
                  // 平滑滚动是增强；锚点本身仍然有效（JS 失效时退化为普通跳转）。
                  ev.preventDefault()
                  t.scrollIntoView({ behavior: 'smooth', block: 'start' })
                }}
              >
                {c.label}
              </a>
            </li>
          ) : null,
        )}
      </ol>
    </nav>
  )
}






