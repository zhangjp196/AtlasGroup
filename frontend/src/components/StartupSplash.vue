<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { playSound } from '@/lib/sound'

const props = withDefaults(defineProps<{ duration?: number }>(), { duration: 5000 })
const emit = defineEmits<{ (e: 'done'): void }>()

const progress = ref(0)
const leaving = ref(false)
const steps = [
  '唤醒本地工作台',
  '加载模型与订阅',
  '连接对话运行时',
  '准备就绪',
]
const step = computed(() => {
  const index = Math.min(steps.length - 1, Math.floor((progress.value / 100) * steps.length))
  return steps[index]
})

let raf = 0
let timer = 0
let leaveTimer = 0
let started = 0

function finishNow(): void {
  if (leaving.value) return
  progress.value = 100
  leaving.value = true
  leaveTimer = window.setTimeout(() => emit('done'), 460)
}

function skip(): void {
  if (leaving.value) return
  finishNow()
}

function tick(now: number): void {
  if (!started) started = now
  const elapsed = now - started
  const ratio = Math.min(1, elapsed / Math.max(600, props.duration))
  // 前段快、后段慢：观感上“快到了”，又不会空转。
  progress.value = Math.min(100, Math.round((1 - Math.pow(1 - ratio, 1.7)) * 100))
  if (ratio < 1) {
    raf = requestAnimationFrame(tick)
  } else {
    finishNow()
  }
}

function onKey(event: KeyboardEvent): void {
  if (event.key === 'Escape' || event.key === 'Enter' || event.key === ' ') skip()
}

onMounted(() => {
  playSound('startup')
  raf = requestAnimationFrame(tick)
  timer = window.setTimeout(finishNow, props.duration + 900)
  window.addEventListener('keydown', onKey)
})

onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  window.clearTimeout(timer)
  window.clearTimeout(leaveTimer)
  window.removeEventListener('keydown', onKey)
})
</script>

<template>
  <div class="boot" :class="{ leaving }" role="status" aria-live="polite" @click="skip">
    <div class="boot-aurora" aria-hidden="true">
      <span class="orb orb-a" />
      <span class="orb orb-b" />
      <span class="orb orb-c" />
    </div>

    <div class="boot-grid" aria-hidden="true" />

    <div class="boot-stage">
      <div class="boot-mark" aria-hidden="true">
        <span class="boot-ring" />
        <span class="boot-ring ring-2" />
        <span class="boot-core">A</span>
        <span class="boot-dot dot-1" />
        <span class="boot-dot dot-2" />
        <span class="boot-dot dot-3" />
      </div>

      <h1 class="boot-name">AtlasX</h1>
      <p class="boot-tag">本地 Agent 工作台</p>

      <div class="boot-bar" aria-hidden="true">
        <span class="boot-bar-fill" :style="{ width: progress + '%' }" />
      </div>
      <div class="boot-meta">
        <span>{{ step }}</span>
        <b>{{ progress }}%</b>
      </div>
    </div>

    <button class="boot-skip" type="button" @click.stop="skip">跳过</button>
  </div>
</template>

<style scoped>
.boot {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: grid;
  place-items: center;
  overflow: hidden;
  background:
    radial-gradient(720px 460px at 50% 38%, color-mix(in srgb, var(--accent) 18%, transparent), transparent 68%),
    radial-gradient(560px 380px at 78% 78%, color-mix(in srgb, var(--accent-2) 14%, transparent), transparent 70%),
    var(--bg);
  transition: opacity .44s ease, transform .44s ease;
}
.boot.leaving { opacity: 0; transform: scale(1.012); pointer-events: none; }

.boot-aurora { position: absolute; inset: -12%; filter: blur(42px); opacity: .8; }
.orb { position: absolute; border-radius: 50%; mix-blend-mode: screen; animation: drift 9s ease-in-out infinite alternate; }
.orb-a { width: 420px; height: 420px; left: 16%; top: 12%; background: radial-gradient(circle, color-mix(in srgb, var(--accent) 48%, transparent), transparent 66%); }
.orb-b { width: 360px; height: 360px; right: 14%; top: 26%; background: radial-gradient(circle, color-mix(in srgb, var(--accent-2) 42%, transparent), transparent 64%); animation-delay: -3s; }
.orb-c { width: 300px; height: 300px; left: 42%; bottom: 8%; background: radial-gradient(circle, color-mix(in srgb, var(--ok) 26%, transparent), transparent 66%); animation-delay: -6s; }

.boot-grid {
  position: absolute; inset: 0; opacity: .18;
  background-image:
    linear-gradient(color-mix(in srgb, var(--text) 10%, transparent) 1px, transparent 1px),
    linear-gradient(90deg, color-mix(in srgb, var(--text) 10%, transparent) 1px, transparent 1px);
  background-size: 46px 46px;
  mask-image: radial-gradient(circle at 50% 42%, #000 0%, transparent 72%);
  -webkit-mask-image: radial-gradient(circle at 50% 42%, #000 0%, transparent 72%);
}

.boot-stage { position: relative; display: flex; flex-direction: column; align-items: center; gap: 10px; padding: 24px; text-align: center; }
.boot-mark { position: relative; width: 132px; height: 132px; display: grid; place-items: center; margin-bottom: 10px; }
.boot-ring {
  position: absolute; inset: 6px; border-radius: 50%;
  border: 1px solid color-mix(in srgb, var(--accent) 46%, transparent);
  border-right-color: transparent; border-bottom-color: transparent;
  animation: spin 2.4s linear infinite;
}
.boot-ring.ring-2 {
  inset: 20px; border-color: color-mix(in srgb, var(--accent-2) 48%, transparent);
  border-left-color: transparent; border-top-color: transparent;
  animation: spin-rev 3.2s linear infinite;
}
.boot-core {
  width: 74px; height: 74px; border-radius: 24px; display: grid; place-items: center;
  font-size: 30px; font-weight: 800; letter-spacing: -.04em;
  color: var(--on-accent);
  background: linear-gradient(140deg, var(--accent), var(--accent-2));
  box-shadow:
    0 18px 46px color-mix(in srgb, var(--accent) 34%, transparent),
    inset 0 1px 0 color-mix(in srgb, #fff 30%, transparent);
  animation: breathe 2.6s ease-in-out infinite;
}
.boot-dot { position: absolute; width: 7px; height: 7px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 14px color-mix(in srgb, var(--accent) 80%, transparent); }
.dot-1 { top: 4px; left: 62px; animation: hop 1.8s ease-in-out infinite; }
.dot-2 { bottom: 16px; left: 12px; background: var(--accent-2); animation: hop 1.8s ease-in-out .35s infinite; }
.dot-3 { right: 8px; bottom: 34px; background: var(--ok); animation: hop 1.8s ease-in-out .7s infinite; }

.boot-name { margin: 0; font-size: clamp(30px, 5vw, 44px); line-height: 1.05; letter-spacing: -.045em; font-weight: 800; }
.boot-tag { margin: 0; color: var(--dim); font-size: 12.5px; letter-spacing: .22em; text-transform: uppercase; }

.boot-bar { width: min(300px, 62vw); height: 4px; margin-top: 22px; border-radius: 99px; background: color-mix(in srgb, var(--text) 12%, transparent); overflow: hidden; }
.boot-bar-fill {
  display: block; height: 100%; width: 0; border-radius: inherit;
  background: linear-gradient(90deg, var(--accent), var(--accent-2));
  box-shadow: 0 0 18px color-mix(in srgb, var(--accent) 60%, transparent);
  transition: width .16s linear;
}
.boot-meta { width: min(300px, 62vw); display: flex; align-items: center; justify-content: space-between; margin-top: 9px; color: var(--faint); font-size: 10.5px; letter-spacing: .06em; }
.boot-meta b { color: var(--dim); font-weight: 600; font-variant-numeric: tabular-nums; }

.boot-skip {
  position: absolute; right: 22px; bottom: 20px;
  padding: 6px 12px; border-radius: 99px; cursor: pointer;
  border: 1px solid var(--line); background: color-mix(in srgb, var(--panel) 70%, transparent);
  color: var(--faint); font-size: 11px; letter-spacing: .08em;
  transition: color .2s ease, border-color .2s ease, transform .2s ease;
}
.boot-skip:hover { color: var(--text); border-color: var(--line-strong); transform: translateY(-1px); }

@keyframes spin { to { transform: rotate(360deg); } }
@keyframes spin-rev { to { transform: rotate(-360deg); } }
@keyframes breathe { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.045); } }
@keyframes hop { 0%, 100% { transform: translateY(0); opacity: .7; } 50% { transform: translateY(-7px); opacity: 1; } }
@keyframes drift { from { transform: translate3d(0, 0, 0) scale(1); } to { transform: translate3d(26px, -18px, 0) scale(1.06); } }

@media (prefers-reduced-motion: reduce) {
  .orb, .boot-ring, .boot-core, .boot-dot { animation: none; }
  .boot.leaving { transform: none; }
}
@media (max-width: 520px) {
  .boot-mark { width: 108px; height: 108px; }
  .boot-core { width: 62px; height: 62px; border-radius: 20px; font-size: 25px; }
  .boot-tag { letter-spacing: .16em; }
}
</style>
