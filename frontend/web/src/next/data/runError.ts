/**
 * 把"这一轮为什么没跑完"翻成人话。
 *
 * 2026-10-19 起因：上游代理通道过载时回的是
 * `Our servers are currently overloaded. Please try again later`，
 * 后端把它原样塞进 SSE 的 error，前端又原样印在首屏 —— 用户看到一句英文，
 * 不知道自己该做什么（等？改问法？重装？）。
 *
 * 这里只做一件事：**按错误形态分档，给出「要你做什么」的那句话**，
 * 原文一个字都不改地留在 detail 里供核对（不加工、不吞掉）。
 * 分档不足时宁可落到「这一轮没有跑完」+ 原文，也不要编一个具体原因。
 */

export type RunErrorView = {
  /** 主句：一句中文，说清发生了什么（不含原文、不含技术术语）。 */
  title: string
  /** 提示：告诉用户下一步动作。空串表示没什么可做的（不渲染空行）。 */
  hint: string
  /** 原文（上游/后端给的话）。恒非空，放在折叠里。 */
  detail: string
  /** 是否值得原话重发。true 才显示「再试一次」。 */
  retryable: boolean
}

/** 通道过载/限流：上游挤、不是请求本身有问题，等十几秒重发大概率能过。 */
const OVERLOAD = /overload|over\s*capacity|servers are currently|rate\s*limit|too many requests|\b429\b|\b503\b|\b529\b|temporarily unavailable|服务繁忙|过载|拥挤|限流/i

/** 超时/黑洞：请求发出去了但没回来，同样可以原话重发。 */
const TIMEOUT = /timed?\s*out|timeout|deadline|超时|75s/i

/** 网络层：请求根本没发出去，或流没建立起来。 */
const NETWORK = /failed to fetch|networkerror|network error|err_connection|请求没有发出去|后端没有返回/i

export function readRunError(raw: string | null | undefined): RunErrorView {
  const detail = (raw ?? '').trim()
  if (!detail) {
    return { title: '这一轮没有跑完', hint: '', detail: '', retryable: false }
  }
  if (OVERLOAD.test(detail)) {
    return {
      title: '上游通道现在很挤，这一轮没跑完',
      hint: '不是你问的问题有问题。等十几秒再点「再试一次」，通常就过了。',
      detail,
      retryable: true,
    }
  }
  if (TIMEOUT.test(detail)) {
    return {
      title: '这一轮等太久了，没等到结果',
      hint: '通道可能在排队。点「再试一次」重发同一句话。',
      detail,
      retryable: true,
    }
  }
  if (NETWORK.test(detail)) {
    return {
      title: '请求没送到服务端',
      hint: '检查一下本机服务是否还在跑，然后点「再试一次」。',
      detail,
      retryable: true,
    }
  }
  return {
    title: '这一轮没有跑完',
    hint: '点「再试一次」重发同一句话；反复失败的话，把下面的原文留着。',
    detail,
    retryable: true,
  }
}
