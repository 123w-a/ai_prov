import type { GuardVM } from '../data/viewModel.ts'
import { isShown } from '../data/viewModel.ts'

/**
 * 安全护栏（架构方案第二节块清单里的 SafetyNotice）。
 *
 * 契约：**只输出节点，不输出容器**。调用方决定它落在哪个容器里——
 * 结果页的常驻右栏、以及散文路径，都是把它塞进一个已有的 `<ul>`。
 * 所以这里返回的是 `<li>` 片段，不是 `<ul>`。这条别改：一旦它自己长出 `<ul>`，
 * 两个调用点会各自被套一层，列表语义就重了。
 *
 * 和红色项同一档处理：进首屏、不折叠。
 */
export function SafetyNotice({ items }: { items: GuardVM[] }) {
  return (
    <>
      {items.map((g, i) => (
        <li key={`g-${i}`} className="alert is-guard">
          <b>{g.condition}</b>
          {isShown(g.text) && <span>{g.text.data}</span>}
        </li>
      ))}
    </>
  )
}
