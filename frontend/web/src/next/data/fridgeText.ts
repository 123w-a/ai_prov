/**
 * 拍照识别的**纯文本层**：把 `/api/fridge/vision` 的返回变成要落进输入框的文本。
 *
 * 为什么单独一个文件（2026-10-14）：`photoInput.ts` 里那个 hook 必须 import
 * `api/client.ts` 才能发请求，而 `client.ts` 用了 `import.meta.env`（Vite 专属），
 * 裸 `node --test` 跑到它就会在模块加载阶段崩掉——连带把这一族纯函数也测不了。
 * 项目里已有先例（`voiceText.ts` 之于 `voiceInput.ts`）：**I/O 与文本变换分家，
 * 文本层单独测**。这里同办。
 *
 * 两个落点共用同一份逐项写法（`fridgeItemParts`），各自只包一层：
 *   · 提问框（今晚房）→ `fridgeDraftText`：一句可以直接改、直接发的人话；
 *   · 补货框（身体档案房）→ `fridgeItemsText`：一份纯清单。
 * 差别不是口味问题：补货框那句下一步是**存**，`addFridge` 会把文本原样交给后端
 * `_split_inventory_text` 切分——把「今天吃点什么？」混进去，末尾那半句就会被
 * 当成一样食材真存进冰箱。
 */

/** 后端 `/api/fridge/vision` 认出来的单样食材。 */
export interface RecognizedItem {
  name: string
  quantity?: string
}

/**
 * 逐项写法（"鸡蛋（3个）"）。名字空的项丢掉；数量读不到就只写名字，
 * 不写空括号（留一对空括号比不写更像 bug）。空清单返回空数组。
 *
 * 这是**唯一**一处把识别结果变成文本的地方，下面两个落点都走它。
 */
export function fridgeItemParts(items: RecognizedItem[]): string[] {
  const parts: string[] = []
  for (const item of items) {
    const name = String(item.name || '').trim()
    if (!name) continue
    const quantity = String(item.quantity || '').trim()
    parts.push(quantity ? `${name}（${quantity}）` : name)
  }
  return parts
}

/**
 * 落点一：**提问框**要的那句话。空清单返回 null（调用方据此不动草稿）。
 *
 * 拼法刻意不写"识别结果：[{...}]"那种机器格式：这句话要落进用户的输入框，
 * 下一步他就该接着改它、发它，所以它必须是一句人话。
 */
export function fridgeDraftText(items: RecognizedItem[]): string | null {
  const parts = fridgeItemParts(items)
  if (parts.length === 0) return null
  return `冰箱里有${parts.join('、')}，今天吃点什么？`
}

/** 落点二：**补货输入框**要的纯清单。空清单返回 null。 */
export function fridgeItemsText(items: RecognizedItem[]): string | null {
  const parts = fridgeItemParts(items)
  return parts.length > 0 ? parts.join('、') : null
}
