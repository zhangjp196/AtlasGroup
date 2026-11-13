import { describe, expect, it } from 'vitest'
import { stripDsml } from './dsml'

const BAR = '\uFF5C'
const BLOCK = [
  `<${BAR}DSML${BAR} calls>`,
  `<${BAR}DSML${BAR} invoke name="session_manage">`,
  `<${BAR}DSML${BAR} parameter name="action" string="true">clear</${BAR}DSML${BAR} parameter>`,
  `</${BAR}DSML${BAR} invoke>`,
  `</${BAR}DSML${BAR} calls>`,
].join('\n')

describe('stripDsml', () => {
  it('removes a full DSML block and keeps surrounding text', () => {
    expect(stripDsml(`先说一句。\n${BLOCK}\n结束了。`)).toBe('先说一句。\n\n结束了。')
  })

  it('removes the ASCII pipe variant', () => {
    const ascii = BLOCK.split(BAR).join('|')
    expect(stripDsml(ascii)).toBe('')
  })

  it('leaves normal text untouched', () => {
    const text = '普通 HTML：<div>hi</div> 和 a < b'
    expect(stripDsml(text)).toBe(text)
  })
})
