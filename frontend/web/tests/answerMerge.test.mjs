import assert from 'node:assert/strict'
import test from 'node:test'

import {
  applyStreamedOpening,
  mergeSyncedAnswer,
  shouldPreferStreamedOpening,
} from '../src/answerMerge.ts'

const TOOL_BUDGET_FALLBACK = (
  '这次没查到足够具体的做法，我先给你一个稳妥的方向：'
  + '优先少油少盐、食材明确、做法简单的一道；'
)

test('server short fallback cannot overwrite the complete streamed opening', () => {
  const streamedOpening = '选中的青椒炒鸡丝要先把鸡肉逆纹切细，再用少量生抽和淀粉抓匀。'
  const local = {
    opening: streamedOpening,
    recipes: [{ name: '青椒炒鸡丝', image_url: null }],
    image_requested: true,
  }
  const server = {
    opening: TOOL_BUDGET_FALLBACK,
    recipes: [{ name: '青椒炒鸡丝', image_url: 'https://images.test/chicken.png' }],
    image_requested: true,
  }

  const merged = mergeSyncedAnswer(local, server)

  assert.equal(merged?.opening, streamedOpening)
  assert.equal(merged?.recipes[0]?.image_url, 'https://images.test/chicken.png')
})

test('server keeps its own opening when it is already the fuller explanation', () => {
  assert.equal(
    shouldPreferStreamedOpening('短句', '这是一段完整且更长的管家讲解。'),
    false,
  )
})

test('authoritative server opening cannot be overwritten by stale streamed text', () => {
  const staleStream = '我这边看不到前文 1/2/3 的具体选项，所以不能确认“3”对应哪一条。'
  const repairedOpening = '你选的第3道是「番茄烘蛋」。'

  assert.equal(
    shouldPreferStreamedOpening(staleStream, repairedOpening, true),
    false,
  )
  assert.equal(
    applyStreamedOpening(
      {
        opening: repairedOpening,
        opening_authoritative: true,
        recipes: [{ name: '番茄烘蛋' }],
      },
      staleStream,
    ).opening,
    repairedOpening,
  )

  const merged = mergeSyncedAnswer(
    {
      opening: staleStream,
      recipes: [{ name: '番茄烘蛋', image_url: null }],
    },
    {
      opening: repairedOpening,
      opening_authoritative: true,
      recipes: [{ name: '番茄烘蛋', image_url: 'https://images.test/fanqie.png' }],
    },
  )

  assert.equal(merged?.opening, repairedOpening)
  assert.equal(merged?.recipes[0]?.image_url, 'https://images.test/fanqie.png')
})
