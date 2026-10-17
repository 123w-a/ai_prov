import { useRef, useState } from 'react'
import { recognizeFridgePhoto } from '../api/client'
import { fridgeDraftText } from '../next/data/fridgeText'
import { NOTHING_RECOGNIZED_NOTE, photoErrorMessage } from '../next/data/photoInput'

/**
 * 默认入口（聊天工作台）工具栏里的「拍照识别」键（2026-10-13）。
 *
 * 它与同排那枚「图片」按钮**不是一回事**，这也是它单独存在的理由：
 *   · 「图片」把照片作为配图交给 `/api/chat` 的主脑（那条路一个字都不动）；
 *   · 本键把照片交给 `/api/fridge/vision` 的视觉模型，**只换回一份食材草稿**。
 * 两者混用会让"我想让主脑看图"变成"我的冰箱被写了一遍"，所以宁可多一枚按钮。
 *
 * 三条纪律（与 src/next/blocks/AskPhoto.tsx 同一套，默认入口不另立规矩）：
 *   · 识别结果只**填进输入框**，绝不自动发送——本组件没有任何通往 `submit()` 的路径；
 *   · 绝不自动写冰箱库存：后端契约为 `draft: true`（api/routes/fridge_route.py:72-81），
 *     替用户确认就是伪造一个他没做过的决定；
 *   · 失败时用后端 `detail` 的**原话**（经 photoErrorMessage 只加一个主语），
 *     不改成"请重试"——那会把"后端模型名配错"说成"你该再试一次"。
 *
 * 草稿文案与失败文案直接复用 `src/next/data/fridgeText.ts` 与 `photoInput.ts` 的纯函数，
 * 不在这里重写一份格式：两套入口对同一份识别结果若拼出两种句子，
 * 用户会看到"同一张照片两个说法"，那种漂移没有测试能兜住。
 * （2026-10-14 注：`fridgeDraftText` 与两个兄弟搬去了 `fridgeText.ts`，好让它们能在
 *   裸 `node --test` 下单独跑——`photoInput.ts` 会连带加载 `api/client.ts`，
 *   而后者用了 Vite 专属的 `import.meta.env`。）
 *
 * 图标内联而不进 `components/Icon.tsx`：那个 icon 集里没有 camera，
 * 而加成员会动到别处也在用的共享文件。本键**只用在这一处**，
 * 与 next 那套"麦克风只服务一处就内联"的取舍一致。线宽/视框照该 icon 集的语汇
 * （24 视框、1.8 描边、currentColor），所以在 18px 上与「图片」「语音」同族。
 */
export function FridgePhotoButton({
  disabled = false,
  onDraft,
  onNotice,
}: {
  disabled?: boolean
  /** 识别成功：把草稿文本交出去（由 ChatArea 决定怎么放进输入框）。 */
  onDraft: (text: string) => void
  /** 进行中 / 成功 / 失败的说明，落在 ChatArea 既有的 composer-status 一行。 */
  onNotice: (message: string) => void
}) {
  const fileRef = useRef<HTMLInputElement | null>(null)
  const [busy, setBusy] = useState(false)

  const accept = async (file: File) => {
    setBusy(true)
    onNotice('正在识别照片…')
    try {
      const items = await recognizeFridgePhoto(file)
      const draft = fridgeDraftText(items)
      if (draft === null) {
        onNotice(NOTHING_RECOGNIZED_NOTE)
        return
      }
      onDraft(draft)
      onNotice(`已认出 ${items.length} 样食材，写进输入框了，确认后再发。`)
    } catch (err) {
      onNotice(photoErrorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      {/* 真的 input[type=file]：按钮只是它的扳机。`capture="environment"` 让手机直接起
          后置摄像头，桌面上浏览器会当成普通选图提示；`accept="image/*"` 是硬要求，
          不给它连视频和文档都会列进相册选择器。 */}
      <input
        ref={fileRef}
        type="file"
        accept="image/*"
        capture="environment"
        hidden
        onChange={(event) => {
          const file = event.target.files?.[0]
          // 清空：同一张图能再选一次（不清会吞掉第二次 change）。
          event.target.value = ''
          if (file) void accept(file)
        }}
      />
      <button
        type="button"
        className="tool-btn"
        disabled={disabled || busy}
        title="拍照或选图，识别冰箱里的食材（只填进输入框，不自动发送）"
        aria-label="拍照识别冰箱里的食材"
        onClick={() => fileRef.current?.click()}
      >
        <svg
          width={18}
          height={18}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.8}
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
          focusable="false"
        >
          <path d="M4 8h3l1.6-2.4h6.8L17 8h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1Z" />
          <circle cx="12" cy="13" r="3.4" />
        </svg>
        {busy ? '识别中' : '拍照识别'}
      </button>
    </>
  )
}
