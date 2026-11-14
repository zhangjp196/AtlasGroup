import { describe, expect, it } from 'vitest'
import { appendWorkspaceFileReference } from './workspaceReference'

describe('appendWorkspaceFileReference', () => {
  it('appends a workspace relative file reference without losing the draft', () => {
    expect(appendWorkspaceFileReference('修复这个测试', 'tests/test_login.py'))
      .toBe('修复这个测试\n请结合工作区文件 `tests/test_login.py` 处理。')
  })

  it('normalizes separators and avoids duplicate references', () => {
    const once = appendWorkspaceFileReference('', 'src\\main.py')
    expect(appendWorkspaceFileReference(once, 'src/main.py')).toBe(once)
  })

  it('rejects absolute and parent-traversal paths', () => {
    expect(appendWorkspaceFileReference('keep this', '/etc/passwd')).toBe('keep this')
    expect(appendWorkspaceFileReference('keep this', '../secret')).toBe('keep this')
    expect(appendWorkspaceFileReference('keep this', 'C:\\secret.txt')).toBe('keep this')
  })
})
