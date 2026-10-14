import { Icon } from '../ui/Icon.tsx'
import { fridgeItemsText } from '../data/fridgeText.ts'
import { usePhotoInput } from '../data/photoInput.ts'

/**
 * 冰箱块的拍照入口（2026-10-14）：把冰箱照片交给后端视觉模型，认出的食材
 * **填进补货输入框**，用户确认后按「存进冰箱」才真写。
 *
 * 与书写面那枚 `.ask-photo-btn` 是**同一条链路、两个落点**，两处差异都写在明处：
 *   · 转写不同：那边要一句人话（`fridgeDraftText` → 「冰箱里有…今天吃点什么？」），
 *     这边要纯清单（`fridgeItemsText`）。把「今天吃点什么？」塞进补货框，用户一点
 *     「存进冰箱」就会把那半句当成一样食材真存进冰箱。
 *   · 提示不同：那边"确认后再**发**"，这边"确认后再**存**"。
 *
 * 形状沿用 `.ask-photo-btn` 的语汇（细描边圆形、无填充、34×34），但**不共用类名**：
 * `ask-` 前缀属于书写面，跨房间借用会让"这块样式归谁"在半年后无人能答。
 *
 * 「识别结果只填进输入框、绝不自动写库存」与书写面同一条纪律：这里没有通往
 * `addFridge` 的路径，写入只发生在用户按下「存进冰箱」之后。
 */
export function FridgePhotoButton({ onRecognized }: { onRecognized: (text: string) => void }) {
  const { state, notice, inputRef, open, accept } = usePhotoInput({
    toText: fridgeItemsText,
    onRecognized,
    successNote: (count) => `已认出 ${count} 样食材，填进补货框了，确认后再存。`,
  })

  const busy = state === 'recognizing'
  const label = busy ? '正在识别照片' : '拍照或选图，认出冰箱里的食材'

  return (
    <span className="pf-fridge-shot">
      {/* `capture="environment"` 让 Android（用户在用的机型）直接起后置摄像头；
          桌面上浏览器会当成普通选图提示。`accept="image/*"` 是硬要求——不给它，
          相册里连视频和文档都会列出来。 */}
      <input
        ref={inputRef}
        className="pf-fridge-shot-file"
        type="file"
        accept="image/*"
        capture="environment"
        aria-label={label}
        onChange={(event) => {
          const file = event.target.files ? event.target.files[0] : null
          // 清空：同一张图能再选一次（不清会吞掉第二次 change）。
          event.target.value = ''
          if (file) void accept(file)
        }}
      />

      <button
        className={busy ? 'pf-fridge-shot-btn is-busy' : 'pf-fridge-shot-btn'}
        type="button"
        aria-label={label}
        title={label}
        disabled={busy}
        onClick={open}
      >
        <Icon name="camera" />
      </button>

      {/* 结果与失败说明：浮层，不参与布局（出现时不推挤输入框与按钮）。
          后端原话可能很长，所以限高、内部滚动，不截断内容。 */}
      {notice && (
        <span className="pf-fridge-shot-note" role="status">
          {notice}
        </span>
      )}
    </span>
  )
}
