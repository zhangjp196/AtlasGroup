<script setup lang="ts">
import { computed } from 'vue'
import { ElButton, ElInput, ElSwitch } from 'element-plus'
import type { MagpieStatus, MagpieTestResult } from '@/api/types'
import ModelFlow from '@/components/ModelFlow.vue'

interface MagpieForm {
  enabled: boolean
  secrets: boolean
  pii: boolean
  paths: boolean
}

defineProps<{
  status: MagpieStatus | null
  saving: boolean
  testing: boolean
  result: MagpieTestResult | null
  totalTokens: number
  modelName: string
  modelTokens: number
}>()

const form = defineModel<MagpieForm>('form', { required: true })
const testText = defineModel<string>('testText', { required: true })
const emit = defineEmits<{ save: []; test: [] }>()

const ruleCount = computed(() => [form.value.secrets, form.value.pii, form.value.paths].filter(Boolean).length)
const kindLabels: Record<string, string> = {
  SECRET: '密钥', JWT: 'JWT', PRIVATEKEY: '私钥', EMAIL: '邮箱',
  PHONE: '手机号', ID: '身份证', IP: 'IP', PATH: '本机路径',
}

function kindSummary(byKind?: Record<string, number>): string {
  return Object.entries(byKind || {})
    .filter(([, count]) => Number(count) > 0)
    .map(([kind, count]) => `${kindLabels[kind] || kind} ${count}`)
    .join(' · ')
}
</script>

<template>
  <div v-if="!status" class="detail-section"><div class="empty">加载本机脱敏状态…</div></div>
  <template v-else>
    <section class="magpie-head">
      <div class="magpie-head-main">
        <div class="magpie-eyebrow">本机脱敏 · MAGPIE</div>
        <h2>敏感信息不出这台电脑</h2>
        <p>
          请求出站前，把密钥、邮箱 / 手机号 / 身份证 / IP、本机绝对路径替换为 ⟦MG_…⟧ 占位符；
          模型回复与工具参数在本机还原。模型只看到占位符。
        </p>
        <div class="magpie-hero-stats">
          <span><b>{{ ruleCount }}</b> 类规则已启用</span>
          <span>规则全部在本机执行</span>
          <span>{{ form.enabled ? '脱敏已开启' : '脱敏已关闭' }}</span>
        </div>
      </div>
      <div class="magpie-head-side">
        <span class="magpie-state" :class="{ on: form.enabled }">
          {{ form.enabled ? '已开启' : '已关闭' }}
        </span>
        <el-switch v-model="form.enabled" :loading="saving" @change="emit('save')" />
      </div>
    </section>

    <ModelFlow
      :total-tokens="totalTokens"
      :model-name="modelName"
      :model-tokens="modelTokens"
      :redacted="Number(status.stats?.redacted_total || 0)"
      :enabled="form.enabled"
    />

    <div class="magpie-grid">
      <section class="detail-section magpie-rules">
        <div class="toolbar">
          <h3 style="margin:0">脱敏规则</h3>
          <span class="role-line">分类开关</span>
        </div>
        <p class="hintline">关闭某类后，对应信息会按原文发送给模型。</p>
        <div class="magpie-rule-list">
          <div class="magpie-rule">
            <div>
              <b>密钥 / Token</b>
              <small>sk-… / ghp_… / JWT / Bearer / 私钥块 / password=…</small>
            </div>
            <el-switch v-model="form.secrets" :disabled="!form.enabled" @change="emit('save')" />
          </div>
          <div class="magpie-rule">
            <div>
              <b>个人信息</b>
              <small>邮箱 / 手机号 / 18 位身份证 / IPv4</small>
            </div>
            <el-switch v-model="form.pii" :disabled="!form.enabled" @change="emit('save')" />
          </div>
          <div class="magpie-rule">
            <div>
              <b>本机绝对路径</b>
              <small>/Users/… / /home/… / C:\Users\…</small>
            </div>
            <el-switch v-model="form.paths" :disabled="!form.enabled" @change="emit('save')" />
          </div>
        </div>
      </section>

      <section class="detail-section magpie-preview-card">
        <div class="toolbar">
          <h3 style="margin:0">脱敏预览</h3>
          <span class="role-line">只在本机执行，不写入统计</span>
        </div>
        <el-input
          v-model="testText"
          type="textarea"
          :rows="4"
          :disabled="!form.enabled"
          placeholder="粘贴一段包含密钥 / 邮箱 / 路径的文本…"
        />
        <div class="magpie-preview-actions">
          <el-button size="small" type="primary" :loading="testing" :disabled="!form.enabled" @click="emit('test')">
            预览脱敏
          </el-button>
          <span v-if="result" class="role-line">
            已脱敏 {{ result.redacted }} 处 · {{ kindSummary(result.by_kind) }}
          </span>
        </div>
        <pre v-if="result" class="magpie-preview">{{ result.masked }}</pre>
        <div v-else class="empty" style="padding:18px 8px">预览结果会显示在这里</div>
      </section>
    </div>

    <p class="hintline magpie-foot">
      占位符映射只在本次 LLM 调用的内存里，不落库。正则识别是尽力而为，
      不保证覆盖所有敏感信息，也不替代隔离环境；脱敏失败时按原文发送并记录日志。
    </p>
  </template>
</template>
