/** Small VT text buffer for the built-in PTY. Handles shell cursor/erase
 * controls without re-rendering raw escape sequences as blank lines. */
function cellWidth(char: string): number {
  const code = char.codePointAt(0) || 0
  if (code === 0x200d || (code >= 0xfe00 && code <= 0xfe0f)
      || (code >= 0x0300 && code <= 0x036f)
      || (code >= 0x1ab0 && code <= 0x1aff)
      || (code >= 0x1dc0 && code <= 0x1dff)
      || (code >= 0x20d0 && code <= 0x20ff)
      || (code >= 0xfe20 && code <= 0xfe2f)) return 0
  if (code >= 0x1100 && (
    code <= 0x115f || code === 0x2329 || code === 0x232a
    || (code >= 0x2e80 && code <= 0xa4cf && code !== 0x303f)
    || (code >= 0xac00 && code <= 0xd7a3)
    || (code >= 0xf900 && code <= 0xfaff)
    || (code >= 0xfe10 && code <= 0xfe19)
    || (code >= 0xfe30 && code <= 0xfe6f)
    || (code >= 0xff00 && code <= 0xff60)
    || (code >= 0xffe0 && code <= 0xffe6)
    || (code >= 0x1f300 && code <= 0x1faff)
    || (code >= 0x20000 && code <= 0x3fffd)
  )) return 2
  return 1
}

export class TerminalBuffer {
  private lines: string[][] = [[]]
  private row = 0
  private col = 0
  private wrapPending = false
  private state: 'text' | 'escape' | 'csi' | 'osc' | 'osc-escape' | 'string' | 'string-escape' = 'text'
  private csi = ''
  private readonly saved = { row: 0, col: 0 }

  constructor(private cols = 120, private readonly maxLines = 2500) {}

  resize(cols: number): void {
    this.cols = Math.max(20, Math.min(500, Math.floor(cols) || 120))
    this.wrapPending = false
  }

  clear(): void {
    this.lines = [[]]
    this.row = 0
    this.col = 0
    this.wrapPending = false
    this.state = 'text'
    this.csi = ''
  }

  write(input: string): void {
    for (const char of input) {
      const code = char.charCodeAt(0)
      if (this.state === 'osc' || this.state === 'string') {
        if (code === 0x07 && this.state === 'osc') this.state = 'text'
        else if (code === 0x1b) this.state = this.state === 'osc' ? 'osc-escape' : 'string-escape'
        continue
      }
      if (this.state === 'osc-escape' || this.state === 'string-escape') {
        this.state = char === '\\' ? 'text' : this.state === 'osc-escape' ? 'osc' : 'string'
        continue
      }
      if (this.state === 'escape') {
        this.state = 'text'
        if (char === '[') { this.state = 'csi'; this.csi = '' }
        else if (char === ']') this.state = 'osc'
        else if ('P_^X'.includes(char)) this.state = 'string'
        else if (char === '7') this.saveCursor()
        else if (char === '8') this.restoreCursor()
        else if (char === 'D') this.lineFeed()
        else if (char === 'E') { this.col = 0; this.lineFeed() }
        else if (char === 'M') this.reverseIndex()
        else if (char === 'c') this.clear()
        continue
      }
      if (this.state === 'csi') {
        if (code >= 0x40 && code <= 0x7e) {
          this.applyCsi(char, this.csi)
          this.state = 'text'
          this.csi = ''
        } else if (this.csi.length < 128) this.csi += char
        else { this.state = 'text'; this.csi = '' }
        continue
      }
      if (code === 0x1b) { this.state = 'escape'; continue }
      if (char === '\r') { this.col = 0; this.wrapPending = false; continue }
      if (char === '\n') { this.lineFeed(); continue }
      if (char === '\b') { this.col = Math.max(0, this.col - 1); this.wrapPending = false; continue }
      if (char === '\t') {
        const count = 8 - (this.col % 8)
        for (let i = 0; i < count; i += 1) this.put(' ')
        continue
      }
      if (code < 0x20 || code === 0x7f) continue
      this.put(char)
    }
  }

  toString(): string {
    return this.lines.map((line) => line.join('')).join('\n')
  }

  private ensureRow(): string[] {
    while (this.lines.length <= this.row) this.lines.push([])
    return this.lines[this.row]
  }

  private put(char: string): void {
    const width = cellWidth(char)
    if (width === 0) {
      const line = this.ensureRow()
      let previous = this.col - 1
      if (line[previous] === '') previous -= 1
      if (previous >= 0) line[previous] = `${line[previous] || ''}${char}`
      return
    }
    if (this.wrapPending) {
      this.row += 1
      this.col = 0
      this.ensureRow()
      this.wrapPending = false
    }
    const line = this.ensureRow()
    if (line[this.col] === '' && this.col > 0) line[this.col - 1] = ' '
    if (cellWidth(line[this.col] || '') === 2) line[this.col + 1] = ' '
    if (width === 2 && cellWidth(line[this.col + 1] || '') === 2) line[this.col + 2] = ' '
    if (width === 2 && line[this.col + 1] === '' && this.col + 1 < line.length) line[this.col + 1] = ' '
    while (line.length < this.col) line.push(' ')
    line[this.col] = char
    if (width === 2) line[this.col + 1] = ''
    this.col += width
    if (this.col >= this.cols) this.wrapPending = true
  }

  private lineFeed(): void {
    this.row += 1
    this.ensureRow()
    this.trimScrollback()
  }

  private reverseIndex(): void {
    this.row = Math.max(0, this.row - 1)
  }

  private trimScrollback(): void {
    if (this.lines.length <= this.maxLines) return
    const count = this.lines.length - this.maxLines
    this.lines.splice(0, count)
    this.row = Math.max(0, this.row - count)
  }

  private saveCursor(): void {
    this.saved.row = this.row
    this.saved.col = this.col
  }

  private restoreCursor(): void {
    this.row = this.bounded(this.saved.row, this.maxLines - 1)
    this.col = this.bounded(this.saved.col, 500)
    this.wrapPending = false
    this.ensureRow()
  }

  private applyCsi(final: string, source: string): void {
    const privateMode = /^[?>!]/.test(source)
    const params = source.replace(/^[?>!]/, '').split(';').map((value) => Number.parseInt(value, 10) || 0)
    const n = (index = 0) => this.bounded(Math.max(1, params[index] || 1), 500)
    const line = () => this.ensureRow()
    switch (final) {
      case 'A': this.row = Math.max(0, this.row - n()); break
      case 'B': this.row = this.bounded(this.row + n(), this.maxLines - 1); this.ensureRow(); break
      case 'C': this.col = this.bounded(this.col + n(), 500); this.wrapPending = false; break
      case 'D': this.col = Math.max(0, this.col - n()); this.wrapPending = false; break
      case 'E': this.row = this.bounded(this.row + n(), this.maxLines - 1); this.col = 0; this.ensureRow(); break
      case 'F': this.row = Math.max(0, this.row - n()); this.col = 0; break
      case 'G': this.col = this.bounded(n() - 1, 500); this.wrapPending = false; break
      case 'd': this.row = this.bounded(n() - 1, this.maxLines - 1); this.ensureRow(); break
      case 'H': case 'f': this.row = this.bounded(n(0) - 1, this.maxLines - 1); this.col = this.bounded(n(1) - 1, 500); this.ensureRow(); this.wrapPending = false; break
      case 'J': this.eraseDisplay(params[0] || 0); break
      case 'K': this.eraseLine(params[0] || 0); break
      case 'P': line().splice(this.col, n()); break
      case '@': line().splice(this.col, 0, ...Array(n()).fill(' ')); break
      case 'X': {
        const current = line()
        for (let i = this.col; i < Math.min(current.length, this.col + n()); i += 1) current[i] = ' '
        break
      }
      case 's': if (!privateMode) this.saveCursor(); break
      case 'u': if (!privateMode) this.restoreCursor(); break
      case 'L': this.lines.splice(this.row, 0, ...Array.from({ length: Math.min(n(), this.maxLines - this.lines.length) }, () => [])); break
      case 'M': this.lines.splice(this.row, n()); if (!this.lines.length) this.lines = [[]]; break
      case 'S': this.lines.splice(0, Math.min(n(), this.lines.length - 1)); this.row = Math.max(0, this.row - n()); break
      case 'T': this.lines.splice(this.row, 0, ...Array.from({ length: Math.min(n(), this.maxLines - this.lines.length) }, () => [])); break
      // SGR (colors/styles), mode changes, and device queries don't affect text layout here.
      default: break
    }
    this.trimScrollback()
  }

  private bounded(value: number, max: number): number {
    return Math.max(0, Math.min(max, Number.isFinite(value) ? Math.floor(value) : 0))
  }

  private eraseLine(mode: number): void {
    const current = this.ensureRow()
    if (mode === 0) current.splice(this.col)
    else if (mode === 1) {
      for (let i = 0; i <= this.col; i += 1) current[i] = ' '
    } else if (mode === 2) current.length = 0
  }

  private eraseDisplay(mode: number): void {
    if (mode === 2 || mode === 3) {
      this.lines = [[]]
      this.row = 0
      this.col = 0
      this.wrapPending = false
    } else if (mode === 0) {
      this.eraseLine(0)
      this.lines.splice(this.row + 1)
    } else if (mode === 1) {
      for (let i = 0; i < this.row; i += 1) this.lines[i] = []
      this.eraseLine(1)
    }
  }
}
