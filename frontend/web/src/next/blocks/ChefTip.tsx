/**
 * 「这一碗的重点」= chef_tip（方案第二节块清单里的 ChefTip）。
 *
 * 23/23 有数据，是"为什么适合我们"最密的载体——这一页唯一一处
 * 直接回答"这顿饭跟我有什么关系"的文字。
 *
 * 降级在调用方：`isShown(vm.chefTip)` 由调用方判并 narrow，块只收 `tip: string`。
 * 这不是绕路——`Display<T>` 的 `.data` 只在 show 分支存在，块内拿不到 guard，
 * TS2339 正是这条边界的直接证据。
 */
export function ChefTip({ tip }: { tip: string }) {
  return (
    <section className="focus">
      <h2 className="focus-title">这一碗的重点</h2>
      <p className="focus-body">{tip}</p>
    </section>
  )
}
