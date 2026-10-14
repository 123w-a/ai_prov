import { Icon } from '../ui/Icon.tsx'
import { fridgeDraftText } from '../data/fridgeText.ts'
import { usePhotoInput } from '../data/photoInput.ts'

/**
 * 书写面里的拍照入口（2026-10-12）：把冰箱/食材照片交给后端视觉模型，
 * 认出什么就**填进输入框**，用户改完自己发。
 *
 * 位置由 AskComposer 决定（`.ask-actions` 内、`.ask-voice` 右侧、`.ask-go` 左侧——
 * 排语音左边的第一版会让语音那行隐私说明读成相机的说明，见 AskComposer 的注释）。
 *
 * 形状按同排那枚麦克风的语汇来：**细描边圆形、无填充、34×34**。几何与
 * `.ask-voice-btn` 在 composer.css 里逐项同值（验收用 getComputedStyle 逐项比对），
 * 不留"两个按钮各调各的"的空间。
 *
 * 图标用 ui/Icon.tsx 的 camera（本文件不内联 svg）——理由：camera 是**通用形状**，
 * 别处（如冰箱房、结果页）迟早要用第二遍；而麦克风当时内联是因为它只服务这一处。
 * 代价是两者线重不同：本文件 1.8/24 视框在 17px 上等效 1.28px，麦克风是 1.4/16
 * 视框在 17px 上等效 1.49px。差 0.2px 的线重在 34px 圆里是量不出来的，
 * 而"同一个形状两处各画一版"是一定会漂的——取前者。
 *
 * 三条降级（每条都有界面表现，不静默）：
 *   · 浏览器不支持 `<input type="file">` 的极端情况 → 仍渲染（这是老 API，
 *     真没有的时候点击自然无反应，不额外伪造支持探测）；
 *   · 选了非图片 / 空文件 → 后端 validate_image_upload 会拒，原因由 data 层带出；
 *   · 后端 502（模型不可用）→ **后端 detail 原文**进浮层，不改成"请重试"。
 *
 * 「识别的字只填回输入框、绝不自动发送」不在本组件里实现：
 * 它把纯文本交给 AskComposer 的插入回调，本组件**没有**任何通往 onSend 的路径。
 */
export function AskPhoto({ onRecognized }: { onRecognized: (text: string) => void }) {
  // 落点是**提问框**：转写成一句人话（`fridgeDraftText`），提示也按"确认后再发"措辞。
  // 档案页那枚按钮落点是补货框，用 `fridgeItemsText` 与"再存"措辞——同一条链路、
  // 两个落点，措辞与写法都必须跟着落点走（见 photoInput.ts 的说明）。
  const { state, notice, inputRef, open, accept } = usePhotoInput({
    toText: fridgeDraftText,
    onRecognized,
  })

  const busy = state === 'recognizing'
  const label = busy ? '正在识别照片' : '拍照或选图，识别冰箱里的食材'

  return (
    <span className="ask-photo">
      {/* 真的 input[type=file]：按钮只是它的扳机。`capture="environment"` 让手机
          （用户在用的 Android）直接起后置摄像头；桌面上浏览器会当成普通选图提示。
          `accept="image/*"` 是硬要求——不给它，相册里连视频和文档都会列出来。 */}
      <input
        ref={inputRef}
        className="ask-photo-file"
        type="file"
        accept="image/*"
        capture="environment"
        aria-label={label}
        onChange={(event) => {
          const file = event.target.files ? event.target.files[0] : null
          // 清空：同一张图 / 同一张照片能再选一次（不清会吞掉第二次 change）。
          event.target.value = ''
          if (file) void accept(file)
        }}
      />

      <button
        className={busy ? 'ask-photo-btn is-busy' : 'ask-photo-btn'}
        type="button"
        aria-label={label}
        title={label}
        disabled={busy}
        onClick={open}
      >
        <Icon name="camera" />
      </button>

      {/* 只有图标变化不算"看得见"：识别中另起一行字（同 AskVoice 的录音态）。 */}
      {busy && (
        <span className="ask-photo-live" role="status">
          正在识别照片…
        </span>
      )}

      {/* 结果与失败说明：浮层，不参与布局（出现时不推挤输入框与按钮）。
          后端原话可能很长，所以浮层限高、内部滚动，不截断内容。 */}
      {notice && (
        <span className="ask-photo-note" role="status">
          {notice}
        </span>
      )}
    </span>
  )
}
