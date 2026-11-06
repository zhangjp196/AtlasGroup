export type SoundKind = 'approval' | 'question' | 'message' | 'done' | 'error' | 'startup'

let audio: AudioContext | null = null

function context(): AudioContext | null {
  if (typeof window === 'undefined') return null
  const Ctor = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  if (!Ctor) return null
  if (!audio) audio = new Ctor()
  return audio
}

function tone(ctx: AudioContext, frequency: number, start: number, duration: number,
              type: OscillatorType = 'sine', volume = 0.06): void {
  const osc = ctx.createOscillator()
  const gain = ctx.createGain()
  osc.type = type
  osc.frequency.setValueAtTime(frequency, start)
  gain.gain.setValueAtTime(0.0001, start)
  gain.gain.exponentialRampToValueAtTime(volume, start + 0.012)
  gain.gain.exponentialRampToValueAtTime(0.0001, start + duration)
  osc.connect(gain)
  gain.connect(ctx.destination)
  osc.start(start)
  osc.stop(start + duration + 0.02)
}

/** 轻量提示音：不依赖音频文件，浏览器首次交互后生效。 */
export function playSound(kind: SoundKind): void {
  try {
    if (typeof localStorage !== 'undefined' && localStorage.getItem('atlasx.sound') === 'off') return
    const ctx = context()
    if (!ctx) return
    if (ctx.state === 'suspended') void ctx.resume()
    const t = ctx.currentTime + 0.01
    if (kind === 'approval') {
      tone(ctx, 880, t, 0.1, 'sine', 0.07)
      tone(ctx, 1175, t + 0.13, 0.14, 'sine', 0.07)
    } else if (kind === 'startup') {
      // 启动上行音：C5 → E5 → G5 → C6，短促但有仪式感。
      tone(ctx, 523.25, t, 0.16, 'sine', 0.045)
      tone(ctx, 659.25, t + 0.16, 0.16, 'sine', 0.045)
      tone(ctx, 783.99, t + 0.32, 0.2, 'triangle', 0.05)
      tone(ctx, 1046.5, t + 0.5, 0.42, 'sine', 0.04)
    } else if (kind === 'question') {
      tone(ctx, 740, t, 0.12, 'sine', 0.055)
      tone(ctx, 740, t + 0.18, 0.12, 'sine', 0.055)
    } else if (kind === 'message') {
      tone(ctx, 520, t, 0.09, 'sine', 0.045)
    } else if (kind === 'done') {
      tone(ctx, 660, t, 0.11, 'sine', 0.055)
      tone(ctx, 880, t + 0.13, 0.16, 'sine', 0.055)
    } else {
      tone(ctx, 220, t, 0.13, 'sawtooth', 0.04)
      tone(ctx, 180, t + 0.16, 0.18, 'sawtooth', 0.04)
    }
  } catch {
    /* 浏览器未解锁音频 / 不支持 Web Audio 时静默 */
  }
}
