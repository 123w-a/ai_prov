/**
 * 「换一批」按钮（2026-10-21）。
 *
 * 单独成块而不是塞进 NearbyList 有两个理由：
 *   ① check-layers 对 blocks 单文件卡 150 行，NearbyList 已经 148 行，再加按钮必然越线；
 *   ② 页码是「检索条件」的一部分，归 useNearby 管；这里只管画一颗按钮、把点击交出去。
 *
 * 只有真有结果时才出现（由调用方决定渲染）：没有结果时按了也没东西可换，
 * 摆一颗按不动的按钮只会让人以为坏了。
 */
export function NearbyMore({
  onMore,
  loading,
}: {
  onMore: () => void
  /** 上一批还在路上时禁用，避免连点把 page 推着连翻。 */
  loading: boolean
}) {
  return (
    <div className="nb-more">
      <button className="fv-btn" type="button" onClick={onMore} disabled={loading}>
        {loading ? '换一批中…' : '换一批'}
      </button>
      <span className="nb-more-hint">没合适的？换一批看看别家</span>
    </div>
  )
}
