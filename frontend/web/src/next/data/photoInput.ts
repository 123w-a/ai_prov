/**
 * 拍照认食材的状态机与副作用（选图/拍照 → POST /api/fridge/vision → 回填草稿）。
 *
 * 结构与 voiceInput.ts 对齐（I/O 只落在 data 层、卸载后不再写状态、说明文字集中导出），
 * 因为两者是同一个位置的两种"把东西拿进来"的方式；行为一旦分叉，用户会看到
 * "这页的相机跟麦克风不一样"，那种不一致极难复现却每天在损耗信任。
 *
 * 三处它与语音**有意不同**，逐条写明理由：
 *   · 能力探测：语音要判 MediaRecorder，缺就整个不渲染（画一枚点了没反应的麦克风
 *     比不画更糟）。`<input type="file">` 是到处都有的老 API，但它**在桌面上不等于
 *     有摄像头**——点了可能只弹出文件选择器。所以按钮照常渲染（挑一张已有的照片
 *     本来就是正当用法），文案写"拍照或选图"，不谎称一定是拍照。
 *   · 结果落地：识别回来的是**食材清单**，不是用户说的句子。它只**填进输入框**、
 *     绝不自动发送（与语音同一条纪律），并且**不写冰箱库存**——后端契约写得很明白：
 *     返回 `draft: true`、note 是"AI 识别草稿，请确认或删改后写入"
 *     （api/routes/fridge_route.py:72-81）。这里替用户确认，就是伪造一个他没做过的决定。
 *   · 失败：后端 502 的 `detail` 原文（实测形如"视觉识别失败：Error code: 404 -
 *     ... Model not exist."）**如实带出**，不改写成"识别失败，请重试"——后者会把
 *     "后端模型名配错了"说成"你该再试一次"，用户会一直重试一个永远不会好的东西。
 *
 * 一处**做不到**的妥协，写在明处：`recognizeFridgePhoto` 没有 signal 参数
 * （本次不改 api/client.ts 的既有函数签名），所以卸载时**无法 abort 在途请求**，
 * 只能靠 aliveRef 挡住"往已卸载组件写状态"。语音那条链路能真 abort，
 * 这一条不能——不等同，不假装等同。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { recognizeFridgePhoto } from '../../api/client.ts'

export type PhotoState = 'idle' | 'recognizing'

/** 识别成功但清单是空的：与语音的空结果同义，**不动草稿**。 */
export const NOTHING_RECOGNIZED_NOTE = '没有认出食材，草稿没有改动。'

/* 文本变换搬去了 `fridgeText.ts`（I/O 与纯函数分家，理由见那个文件头）——
   本文件只剩状态机与副作用，测试也就不必连带加载 `api/client.ts`。 */
import { type RecognizedItem } from './fridgeText.ts'

export type { RecognizedItem } from './fridgeText.ts'

/**
 * 失败原因如实成句。后端给的是**它自己的原话**（jsonRequest 把 `detail` 抛出），
 * 这里只加一个能读懂的主语，不改写它的内容。
 */
export function photoErrorMessage(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err)
  return `照片识别没有成功：${raw}`
}

export interface PhotoInputConfig {
  /** 识别结果 → 要落进那个输入框的文本。返回 null = 清单为空，不动输入框。
   *  两个落点各有一个实现：`fridgeDraftText`（提问框）/ `fridgeItemsText`（补货框）。 */
  toText: (items: RecognizedItem[]) => string | null
  /** 拿到文本后往哪儿放（插进提问框 / 填进补货框）。
   *  2026-10-22：同时把结构化清单交给调用方——提问框那条路会把它随 `/api/chat`
   *  一起发回后端，让 chef_think 注入「只能基于这份清单」的硬约束；补货框那条
   *  路径用不上这个参数（存的是文本），忽略即可。 */
  onRecognized: (text: string, items?: RecognizedItem[]) => void
  /** 成功提示的措辞。落点不同措辞就得不同——提问框是"发"、补货框是"存"，
   *  共用一句会把用户带到错的下一步。 */
  successNote?: (count: number) => string
}

/** 默认提示（提问框落点）。 */
const defaultSuccessNote = (count: number) =>
  `已认出 ${count} 样食材，写进输入框了，确认后再发。`

export function usePhotoInput({ toText, onRecognized, successNote }: PhotoInputConfig) {
  const [state, setState] = useState<PhotoState>('idle')
  const [notice, setNotice] = useState<string | null>(null)

  /** 隐藏的 `<input type="file">`：按钮点它，真实的文件选择/相机由浏览器给。 */
  const inputRef = useRef<HTMLInputElement | null>(null)
  const aliveRef = useRef(true)
  /** 三个回调都放进 ref：识别回来时用的是**当时**那套函数，不因父组件重渲染而
   *  失联；`accept` 也因此能保持空依赖的稳定引用（换落点时不必重新建它）。 */
  const toTextRef = useRef(toText)
  toTextRef.current = toText
  const cbRef = useRef(onRecognized)
  cbRef.current = onRecognized
  const noteRef = useRef(successNote ?? defaultSuccessNote)
  noteRef.current = successNote ?? defaultSuccessNote

  const open = useCallback(() => {
    setNotice(null)
    inputRef.current?.click()
  }, [])

  /** 一张图进来了 → 真上传 → 真解析 → 真回填。任何一条失败都如实写出来。 */
  const accept = useCallback(async (file: File) => {
    setState('recognizing')
    setNotice(null)
    try {
      const items = await recognizeFridgePhoto(file)
      if (!aliveRef.current) return
      const draft = toTextRef.current(items)
      if (draft === null) {
        setNotice(NOTHING_RECOGNIZED_NOTE)
        return
      }
      cbRef.current(draft, items)
      setNotice(noteRef.current(items.length))
    } catch (err) {
      if (!aliveRef.current) return
      setNotice(photoErrorMessage(err))
    } finally {
      if (aliveRef.current) setState('idle')
    }
  }, [])

  /** 说明不会永久占位：浮层 14 秒后自己退场（后端原话较长，比语音多给 2 秒）。 */
  useEffect(() => {
    if (!notice) return
    const timer = window.setTimeout(() => setNotice(null), 14000)
    return () => window.clearTimeout(timer)
  }, [notice])

  /** 卸载 = 不再往这个组件里写状态（在途请求无法 abort，理由见文件头）。 */
  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
    }
  }, [])

  return { state, notice, inputRef, open, accept }
}
