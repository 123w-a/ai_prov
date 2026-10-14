import assert from 'node:assert/strict'
import { test } from 'node:test'

import { fridgeDraftText, fridgeItemParts, fridgeItemsText } from './fridgeText.ts'

/* 这一族纯函数此前没有测试，2026-10-14 加第二个落点（补货框）时补上。
   （它跟 `photoInput.ts` 分家就是为了能被这样单独跑：那个 hook 必须 import
   `api/client.ts`，而后者用 `import.meta.env`，裸 node --test 加载即崩。）

   最容易出的错不是"少了括号"，而是**把提问框那句话喂给补货框**：`addFridge`
   会把文本原样交给后端 `_split_inventory_text` 切分，混进「今天吃点什么？」
   就会被当成一样食材真存进冰箱。最后两条测试专门盯这件事。 */

test('fridgeItemParts：数量读得到就带在括号里', () => {
  assert.deepEqual(fridgeItemParts([{ name: '鸡蛋', quantity: '3个' }]), ['鸡蛋（3个）'])
})

test('fridgeItemParts：读不到数量就只写名字，不留一对空括号', () => {
  assert.deepEqual(fridgeItemParts([{ name: '青菜' }]), ['青菜'])
  assert.deepEqual(fridgeItemParts([{ name: '青菜', quantity: '   ' }]), ['青菜'])
})

test('fridgeItemParts：名字为空的项整体丢掉，不产出「（3个）」这种孤儿', () => {
  assert.deepEqual(fridgeItemParts([{ name: '  ', quantity: '3个' }, { name: '鸡蛋' }]), ['鸡蛋'])
})

test('fridgeItemParts：空清单给空数组', () => {
  assert.deepEqual(fridgeItemParts([]), [])
})

test('fridgeDraftText：拼成提问框那句人话', () => {
  assert.equal(
    fridgeDraftText([{ name: '鸡蛋' }, { name: '西兰花' }]),
    '冰箱里有鸡蛋、西兰花，今天吃点什么？',
  )
})

test('fridgeDraftText：空清单返回 null（调用方据此不动草稿）', () => {
  assert.equal(fridgeDraftText([]), null)
  assert.equal(fridgeDraftText([{ name: '' }]), null)
})

test('fridgeItemsText：给补货框纯清单，绝不带上提问框那半句', () => {
  const text = fridgeItemsText([{ name: '鸡蛋', quantity: '3个' }, { name: '青菜' }])
  assert.equal(text, '鸡蛋（3个）、青菜')
  assert.ok(!text!.includes('今天吃点什么'))
  assert.ok(!text!.includes('冰箱里有'))
})

test('fridgeItemsText：空清单返回 null', () => {
  assert.equal(fridgeItemsText([]), null)
  assert.equal(fridgeItemsText([{ name: '   ' }]), null)
})

test('两个落点共用同一份逐项写法，不会各自漂', () => {
  const items = [{ name: '鸡蛋', quantity: '3个' }, { name: '', quantity: '2个' }, { name: '青菜' }]
  const parts = fridgeItemParts(items)
  assert.equal(fridgeDraftText(items), `冰箱里有${parts.join('、')}，今天吃点什么？`)
  assert.equal(fridgeItemsText(items), parts.join('、'))
})
