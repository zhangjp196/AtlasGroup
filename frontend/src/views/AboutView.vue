<script setup lang="ts">
import { computed, onMounted } from 'vue'
import PageHeader from '@/components/PageHeader.vue'
import { useDataStore } from '@/stores/data'

const data = useDataStore()
onMounted(() => { void data.loadInfo() })

const activeModel = computed(() => {
  const code = data.info?.llm?.active_model || ''
  const model = (data.info?.llm?.models || []).find((item) => item.code === code)
  return model?.effective_model || model?.model || data.info?.llm?.model || code || '未配置'
})
</script>

<template>
  <PageHeader section="关于" title="关于 AtlasX" chip="本地单用户">
  </PageHeader>

  <div class="canvas-body">
    <div class="about-page">
      <div class="about-head">
        <span class="about-logo">A</span>
        <div>
          <h1>{{ data.info?.name || 'AtlasX' }}</h1>
          <p>本地单用户 Agent 工作台 · {{ data.info?.version || '3.7.0' }}</p>
        </div>
      </div>

      <div class="about-info">
        <div><span>运行模式</span><b>{{ data.info?.mode || 'local' }} · {{ data.info?.name || 'AtlasX' }}</b></div>
        <div><span>当前模型</span><b>{{ activeModel }}<template v-if="data.info?.llm?.active_model"> （{{ data.info.llm.active_model }}）</template></b></div>
        <div><span>数据库</span><b class="mono">{{ data.info?.db_path || '—' }}</b></div>
        <div><span>配置</span><b class="mono">{{ data.info?.config_path || '—' }}</b></div>
      </div>

      <p class="about-note">模型 / 订阅 / MCP / 技能 / 用量等设置：点左下角齿轮。</p>
    </div>
  </div>
</template>
