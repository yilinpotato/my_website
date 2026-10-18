<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:3000'

const auth = useAuthStore()
const router = useRouter()

const sourceType = ref('note')
const selectedId = ref('')

const noteEntries = ref([])
const errorEntries = ref([])
const loadStatus = ref('idle')
const generating = ref(false)
const errorMessage = ref('')

const mapRootId = ref(null)
const mapNodes = ref([])
const mapEdges = ref([])
const highlightCounts = ref({})
const related = ref({})
const evidence = ref({})
const analysis = ref({})

const chartEl = ref(null)
const chartInstance = ref(null)
const mermaidSvg = ref('')

const activeId = ref(null)

const ensureAuth = async () => {
  if (!auth.token) {
    router.push('/login')
    return false
  }
  try {
    await auth.fetchProfile()
    return true
  } catch {
    router.push('/login')
    return false
  }
}

const authedFetchJson = async (path, options = {}) => {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      ...(options.headers || {}),
      Authorization: `Bearer ${auth.token}`,
    },
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    if (res.status === 401) {
      auth.logout()
      router.push('/login')
      throw new Error('登录已过期，请重新登录')
    }
    throw new Error(data?.message || '请求失败')
  }
  return data
}

const loadSources = async () => {
  if (!(await ensureAuth())) return
  loadStatus.value = 'loading'
  errorMessage.value = ''
  try {
    const [notes, errors] = await Promise.all([
      authedFetchJson('/api/note/entries'),
      authedFetchJson('/api/error-book/entries'),
    ])
    noteEntries.value = Array.isArray(notes) ? notes : notes?.items || []
    errorEntries.value = Array.isArray(errors) ? errors : errors?.items || []
    loadStatus.value = 'ready'
  } catch (e) {
    loadStatus.value = 'failed'
    errorMessage.value = e?.message || '加载失败'
  }
}

onMounted(loadSources)

const availableEntries = computed(() => (sourceType.value === 'note' ? noteEntries.value : errorEntries.value))

const nodeById = computed(() => {
  const m = new Map()
  for (const n of mapNodes.value || []) m.set(Number(n.id), n)
  return m
})

const childrenByFrom = computed(() => {
  const m = new Map()
  for (const e of mapEdges.value || []) {
    const from = Number(e.from)
    const to = Number(e.to)
    if (!m.has(from)) m.set(from, [])
    m.get(from).push(to)
  }
  return m
})

const highlightCountOf = (nodeId) => Number(highlightCounts.value?.[String(nodeId)] || 0)

const mermaidCode = computed(() => {
  const root = Number(mapRootId.value)
  if (!root || !(mapEdges.value || []).length) return ''

  const labelOf = (id) => {
    const n = nodeById.value.get(Number(id))
    const name = (n?.name || '').replace(/\"/g, '\\"')
    const weak = highlightCountOf(id)
    return weak ? `${name}（错因×${weak}）` : name
  }

  const lines = ['graph TD']
  // node declarations for labels
  const ids = new Set([root])
  for (const e of mapEdges.value || []) {
    ids.add(Number(e.from))
    ids.add(Number(e.to))
  }
  for (const id of Array.from(ids)) {
    const safe = `n${id}`
    const label = labelOf(id)
    if (label) lines.push(`${safe}["${label}"]`)
  }
  for (const e of mapEdges.value || []) {
    lines.push(`n${Number(e.from)} --> n${Number(e.to)}`)
  }
  return lines.join('\n')
})

const traversal = computed(() => {
  const root = Number(mapRootId.value)
  if (!root || !nodeById.value.size) return []
  const result = []
  const walk = (id, depth = 0, visited = new Set()) => {
    if (visited.has(id)) return
    visited.add(id)
    const node = nodeById.value.get(id)
    if (!node) return
    result.push({
      id,
      title: node.name,
      depth,
      kind: node.kind,
      subject: node.subject,
      highlight: highlightCountOf(id),
    })
    const children = childrenByFrom.value.get(id) || []
    children.forEach((child) => walk(child, depth + 1, visited))
  }
  walk(root)
  return result
})

const activeNode = computed(() => {
  const id = Number(activeId.value)
  if (!id) return null
  return nodeById.value.get(id) || null
})

const activeRelated = computed(() => {
  const id = Number(activeId.value)
  if (!id) return []
  const items = related.value?.[String(id)]
  return Array.isArray(items) ? items : []
})

const activeEvidence = computed(() => {
  const id = Number(activeId.value)
  if (!id) return { notes: [], errors: [] }
  const ev = evidence.value?.[String(id)]
  if (!ev || typeof ev !== 'object') return { notes: [], errors: [] }
  return {
    notes: Array.isArray(ev.notes) ? ev.notes : [],
    errors: Array.isArray(ev.errors) ? ev.errors : [],
  }
})

const activeAnalysis = computed(() => {
  const id = Number(activeId.value)
  if (!id) return null
  const a = analysis.value?.[String(id)]
  return a && typeof a === 'object' ? a : null
})

const selectNode = (id) => {
  activeId.value = Number(id)
}

const treeData = computed(() => {
  const root = Number(mapRootId.value)
  if (!root) return null
  const nodes = nodeById.value
  const childrenMap = childrenByFrom.value

  const colorOfKind = (kind) => {
    const k = String(kind || 'concept')
    if (k === 'chapter') return '#6f9dec'
    if (k === 'concept') return '#8bd4c8'
    if (k === 'method') return '#f7c66a'
    if (k === 'mistake') return '#f28b82'
    return '#8bd4c8'
  }

  const walk = (id, visited = new Set()) => {
    if (visited.has(id)) return null
    visited.add(id)

    const n = nodes.get(Number(id))
    if (!n) return null

    const weak = highlightCountOf(id)
    const base = 18
    const size = Math.min(46, base + weak * 4)
    const kind = String(n.kind || 'concept')

    const childIds = childrenMap.get(Number(id)) || []
    const children = []
    for (const cid of childIds) {
      const child = walk(Number(cid), visited)
      if (child) children.push(child)
    }

    return {
      id: Number(id),
      name: n.name,
      kind,
      subject: n.subject,
      weak,
      symbolSize: size,
      itemStyle: { color: colorOfKind(kind) },
      children,
    }
  }

  return walk(root)
})

const generateMindMap = async () => {
  if (!(await ensureAuth())) return
  if (!selectedId.value) {
    errorMessage.value = '请先选择一条笔记或错题记录'
    return
  }
  generating.value = true
  errorMessage.value = ''
  try {
    const data = await authedFetchJson('/api/mind-map/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        source_type: sourceType.value,
        source_id: Number(selectedId.value),
        mode: 'ai',
      }),
    })

    mapRootId.value = data?.root_id || null
    mapNodes.value = Array.isArray(data?.nodes) ? data.nodes : []
    mapEdges.value = Array.isArray(data?.edges) ? data.edges : []

    const hc = {}
    for (const h of data?.highlights || []) {
      if (h?.node_id != null) hc[String(h.node_id)] = Number(h.count || 0)
    }
    highlightCounts.value = hc
    related.value = data?.related || {}
    evidence.value = data?.evidence || {}
    analysis.value = data?.analysis || {}

    activeId.value = Number(data?.root_id || null)

    // 生成后直接展示：Mermaid 大图 + 目录 + ECharts 知识图谱
    await nextTick()
    // 让布局先稳定（避免容器宽高为 0 导致 ECharts 白屏）
    await new Promise((resolve) => requestAnimationFrame(() => resolve()))
    await Promise.all([renderEcharts(), renderMermaid()])
  } catch (e) {
    errorMessage.value = e?.message || '生成失败'
  } finally {
    generating.value = false
  }
}

const renderEcharts = async () => {
  if (!chartEl.value) return
  if (!mapRootId.value) return
  if (!treeData.value) return

  const echarts = await import('echarts')
  if (!chartInstance.value) {
    chartInstance.value = echarts.init(chartEl.value)
    chartInstance.value.on('click', (params) => {
      if (params?.dataType === 'node' && params?.data?.id != null) {
        selectNode(params.data.id)
      }
    })
  }

  chartInstance.value.setOption(
    {
      tooltip: {
        formatter: (p) => {
          if (p?.dataType !== 'node') return ''
          const d = p.data || {}
          const subject = d?.subject || '未分类'
          const weak = Number(d?.weak || 0)
          const kind = String(d?.kind || 'concept')
          return `${d?.name || ''}<br/>类型：${kind}<br/>科目：${subject}<br/>错因命中：${weak}`
        },
      },
      series: [
        {
          type: 'tree',
          data: [treeData.value],
          top: 24,
          left: 24,
          bottom: 24,
          right: 24,
          layout: 'orthogonal',
          orient: 'LR',
          roam: true,
          expandAndCollapse: true,
          initialTreeDepth: -1,
          symbol: 'circle',
          symbolSize: (v, params) => Number(params?.data?.symbolSize || 18),
          label: {
            position: 'right',
            verticalAlign: 'middle',
            align: 'left',
            color: '#0f1d40',
            fontSize: 13,
          },
          labelLayout: { hideOverlap: true },
          lineStyle: {
            width: 2.2,
            color: 'rgba(111, 157, 236, 0.6)',
          },
          emphasis: {
            focus: 'descendant',
          },
        },
      ],
    },
    true
  )

  // 切换视图/首次渲染后做一次 resize，避免 0-size
  try {
    chartInstance.value.resize?.()
  } catch {
    // ignore
  }
}

const renderMermaid = async () => {
  mermaidSvg.value = ''
  if (!mermaidCode.value) return
  const mermaid = (await import('mermaid')).default
  // 禁止把 SVG 缩放到容器宽度（否则大图会被压缩得很小）
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: 'strict',
    flowchart: { useMaxWidth: false },
  })
  try {
    const id = `mm_${Date.now()}`
    const { svg } = await mermaid.render(id, mermaidCode.value)
    mermaidSvg.value = svg
  } catch {
    mermaidSvg.value = ''
  }
}
watch(
  () => [mapNodes.value, mapEdges.value, highlightCounts.value],
  async () => {
    await nextTick()
    await new Promise((resolve) => requestAnimationFrame(() => resolve()))
    await Promise.all([renderEcharts(), renderMermaid()])
  },
  { deep: true }
)

const handleResize = () => {
  try {
    chartInstance.value?.resize?.()
  } catch {
    // ignore
  }
}

onMounted(() => {
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  try {
    chartInstance.value?.dispose?.()
  } catch {
    // ignore
  }
  chartInstance.value = null
})
</script>

<template>
  <section class="mind-hero">
    <div>
      <p class="eyebrow">Mind Map View</p>
      <h1>知识点 → 心智图，一键串联</h1>
      <p>
        基于笔记与错题内容生成知识树，并可适度补充相关知识点，帮助梳理概念与方法。
      </p>
      <div class="hero-actions">
        <RouterLink class="secondary" to="/">返回首页</RouterLink>
        <a class="primary" href="#map">查看心智图</a>
      </div>
    </div>
    <div class="legend">
      <span class="pill" style="--color:#6f9dec">章节</span>
      <span class="pill" style="--color:#8bd4c8">概念</span>
      <span class="pill" style="--color:#f7c66a">方法</span>
      <span class="pill" style="--color:#f28b82">错因</span>
    </div>
  </section>

  <section class="map-shell" id="map">
    <article class="outline">
      <header>
        <p class="eyebrow">结构目录</p>
        <h2>选择来源并生成知识树</h2>
      </header>

      <div class="controls">
        <label>
          <span>来源</span>
          <select v-model="sourceType" :disabled="generating">
            <option value="note">笔记</option>
            <option value="error_book">错题</option>
          </select>
        </label>
        <label>
          <span>记录</span>
          <select v-model="selectedId" :disabled="generating || loadStatus !== 'ready'">
            <option value="">请选择…</option>
            <option v-for="e in availableEntries" :key="e.id" :value="String(e.id)">
              {{ e.title || `#${e.id}` }}
            </option>
          </select>
        </label>
        <button type="button" class="primary" :disabled="generating || !selectedId" @click="generateMindMap">
          {{ generating ? '生成中…' : '生成知识树' }}
        </button>
      </div>

      <p v-if="errorMessage" class="error">{{ errorMessage }}</p>

      <div class="block">
        <p class="eyebrow">目录结构</p>
        <ul>
        <li
          v-for="item in traversal"
          :key="item.id"
          :style="{ paddingLeft: `${item.depth * 1.2}rem` }"
        >
          <button type="button" :class="{ active: Number(activeId) === item.id }" @click="selectNode(item.id)">
            <span class="title">{{ item.title }}</span>
            <span v-if="item.highlight" class="weak">错因×{{ item.highlight }}</span>
          </button>
        </li>
        </ul>
      </div>

      <div class="block">
        <p class="eyebrow">Mermaid 大图</p>
        <p class="hint">生成后会自动显示，便于直接截图/粘贴到文档。</p>
        <div v-if="mermaidSvg" class="mermaid-render" v-html="mermaidSvg" />
        <p v-else class="muted">暂无 Mermaid 图（请先生成知识树）。</p>
      </div>

      <div class="block">
        <p class="eyebrow">ECharts 知识图谱</p>
        <div class="chart-wrap">
          <div ref="chartEl" class="chart"></div>
          <p class="hint">关系图谱：支持拖拽、缩放、点击节点查看详情。</p>
        </div>
      </div>

      <div class="block">
        <p class="eyebrow">Mermaid 源码</p>
        <p class="hint">可复制到 Markdown 文档中渲染：</p>
        <pre class="code">{{ mermaidCode }}</pre>
      </div>
    </article>
    <article class="node-panel">
      <header>
        <p class="eyebrow">节点详情</p>
        <h2>{{ activeNode?.name || '请选择一个节点' }}</h2>
        <span v-if="activeNode" class="type-pill" :data-type="activeNode.kind">{{ activeNode.kind }}</span>
      </header>
      <p v-if="activeNode" class="meta">科目：{{ activeNode.subject || '未分类' }}</p>
      <p v-if="activeNode" class="meta" v-show="highlightCountOf(activeNode.id)">
        薄弱提示：最近错因命中 {{ highlightCountOf(activeNode.id) }} 次
      </p>

      <div v-if="activeNode" class="child-info">
        <p>相关知识点</p>
        <div class="chips" v-if="activeRelated.length">
          <button
            v-for="r in activeRelated"
            :key="r.node_id"
            type="button"
            class="chip"
            @click="selectNode(r.node_id)"
          >
            {{ r.name }} · {{ r.count }}
          </button>
        </div>
        <p v-else class="muted">暂无关联记录（继续积累笔记/错题会更准）。</p>
      </div>

      <div v-if="activeNode" class="evidence">
        <p>证据引用</p>
        <div class="ev-grid">
          <div class="ev-col">
            <p class="ev-title">相关笔记</p>
            <ul v-if="activeEvidence.notes.length" class="ev-list">
              <li v-for="n in activeEvidence.notes" :key="n.id">
                <span class="ev-name">{{ n.title || `笔记#${n.id}` }}</span>
                <span class="ev-meta">{{ n.created_at }}</span>
              </li>
            </ul>
            <p v-else class="muted">暂无匹配的历史笔记。</p>
          </div>
          <div class="ev-col">
            <p class="ev-title">相关错题</p>
            <ul v-if="activeEvidence.errors.length" class="ev-list">
              <li v-for="e in activeEvidence.errors" :key="e.id">
                <span class="ev-name">{{ e.title || `错题#${e.id}` }}</span>
                <span class="ev-meta">{{ e.created_at }}</span>
                <span v-if="e.verdict" class="ev-tag">{{ e.verdict }}</span>
              </li>
            </ul>
            <p v-else class="muted">暂无匹配的历史错题。</p>
          </div>
        </div>
      </div>

      <div v-if="activeNode" class="analysis">
        <p>对比分析（AI）</p>
        <p v-if="activeAnalysis?.summary" class="analysis-summary">{{ activeAnalysis.summary }}</p>
        <p v-else class="muted">暂无对比分析（需要更多历史证据或稍后再试）。</p>
        <div v-if="activeAnalysis" class="analysis-grid">
          <div>
            <p class="ev-title">差距/误区</p>
            <ul v-if="Array.isArray(activeAnalysis.gaps) && activeAnalysis.gaps.length" class="ev-list">
              <li v-for="(g, idx) in activeAnalysis.gaps" :key="idx">{{ g }}</li>
            </ul>
            <p v-else class="muted">—</p>
          </div>
          <div>
            <p class="ev-title">复习建议</p>
            <ul v-if="Array.isArray(activeAnalysis.actions) && activeAnalysis.actions.length" class="ev-list">
              <li v-for="(a, idx) in activeAnalysis.actions" :key="idx">{{ a }}</li>
            </ul>
            <p v-else class="muted">—</p>
          </div>
        </div>
      </div>
    </article>
  </section>
</template>

<style scoped>
.mind-hero,
.map-shell,
.map-shell {
  background: rgba(255, 255, 255, 0.95);
  border-radius: 2rem;
  padding: clamp(1.5rem, 4vw, 3rem);
  box-shadow: 0 25px 60px rgba(21, 47, 89, 0.08);
  margin-bottom: 2rem;
}

.mind-hero {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 1rem;
}

.legend {
  display: flex;
  gap: 0.6rem;
  flex-wrap: wrap;
  align-items: center;
}

.pill {
  padding: 0.4rem 0.9rem;
  border-radius: 999px;
  font-weight: 600;
  background: color-mix(in srgb, var(--color), rgba(255, 255, 255, 0.2));
  border: 1px solid color-mix(in srgb, var(--color), rgba(0, 0, 0, 0.2));
  color: #0f1d40;
}

.hero-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.8rem;
  margin-top: 1.2rem;
}

.primary,
.secondary {
  border-radius: 999px;
  padding: 0.8rem 1.6rem;
  font-weight: 600;
}

.primary {
  background: linear-gradient(120deg, #6f9dec, #8bd4c8);
  color: #fff;
}

.secondary {
  border: 1px solid rgba(17, 62, 125, 0.25);
  color: #0c2b53;
}

.map-shell {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 1.5rem;
}

.outline,
.node-panel {
  background: rgba(248, 251, 255, 0.9);
  border-radius: 1.5rem;
  padding: 1.5rem;
  border: 1px solid rgba(111, 157, 236, 0.16);
}

@media (min-width: 900px) {
  .node-panel {
    position: sticky;
    top: 1.25rem;
    align-self: start;
    max-height: calc(100vh - 2.5rem);
    overflow: auto;
  }
}

.block {
  margin-top: 1rem;
}

.outline ul {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.outline button {
  width: 100%;
  text-align: left;
  background: transparent;
  border: 1px solid transparent;
  padding: 0.4rem 0.6rem;
  border-radius: 0.6rem;
  cursor: pointer;
  color: #0f1d40;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.6rem;
}

.title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.weak {
  border-radius: 999px;
  padding: 0.15rem 0.55rem;
  border: 1px solid rgba(242, 139, 130, 0.55);
  background: rgba(242, 139, 130, 0.12);
  font-size: 0.82rem;
  flex: 0 0 auto;
}

.controls {
  display: grid;
  grid-template-columns: 1fr 1fr auto;
  gap: 0.8rem;
  align-items: end;
  margin: 1rem 0 0.8rem;
}

.controls label {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  font-size: 0.9rem;
  color: rgba(29, 60, 120, 0.9);
}

.controls select {
  border-radius: 0.8rem;
  padding: 0.6rem 0.8rem;
  border: 1px solid rgba(111, 157, 236, 0.25);
  background: rgba(255, 255, 255, 0.9);
  color: #0f1d40;
}

.controls .primary {
  padding: 0.7rem 1.2rem;
}

.mode-switch {
  display: flex;
  gap: 0.6rem;
  margin: 0.2rem 0 0.8rem;
}

.ghost.active {
  border-color: rgba(111, 157, 236, 0.6);
  background: rgba(111, 157, 236, 0.12);
}

.chart-wrap {
  margin-top: 0.4rem;
}

.chart {
  width: 100%;
  height: 420px;
  border-radius: 1rem;
  border: 1px solid rgba(111, 157, 236, 0.16);
  background: rgba(255, 255, 255, 0.9);
}

.mermaid-wrap {
  margin-top: 0.4rem;
}

.code {
  background: rgba(15, 22, 40, 0.92);
  color: #e8f0ff;
  padding: 0.9rem;
  border-radius: 1rem;
  font-size: 0.85rem;
  max-height: 220px;
  overflow: auto;
}

.mermaid-render {
  margin-top: 0.8rem;
  padding: 0.8rem;
  border-radius: 1rem;
  border: 1px solid rgba(111, 157, 236, 0.16);
  background: rgba(255, 255, 255, 0.9);
  overflow: auto;
  min-height: 420px;
  max-height: 70vh;
}

.mermaid-render :deep(svg) {
  display: block;
  max-width: none;
  height: auto;
  min-width: 1200px;
}

.hint {
  margin-top: 0.6rem;
  color: rgba(15, 29, 64, 0.72);
}

.error {
  margin: 0.2rem 0 0.8rem;
  padding: 0.6rem 0.8rem;
  border-radius: 0.9rem;
  border: 1px solid rgba(242, 139, 130, 0.55);
  background: rgba(242, 139, 130, 0.12);
  color: #0f1d40;
}

.outline button.active {
  border-color: rgba(111, 157, 236, 0.6);
  background: rgba(111, 157, 236, 0.15);
}

.node-panel ul {
  list-style: none;
  padding: 0;
  margin: 1rem 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.node-panel li::before {
  content: '• ';
  color: #6f9dec;
}

.type-pill {
  border-radius: 999px;
  padding: 0.2rem 0.8rem;
  border: 1px solid rgba(15, 39, 88, 0.2);
  font-size: 0.8rem;
  text-transform: capitalize;
}

.child-info {
  margin-top: 1rem;
}

.evidence,
.analysis {
  margin-top: 1rem;
  padding-top: 1rem;
  border-top: 1px dashed rgba(111, 157, 236, 0.22);
}

.ev-grid,
.analysis-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 0.9rem;
  margin-top: 0.6rem;
}

@media (min-width: 900px) {
  .ev-grid,
  .analysis-grid {
    grid-template-columns: 1fr 1fr;
  }
}

.ev-title {
  margin: 0 0 0.35rem;
  font-weight: 650;
  color: rgba(15, 29, 64, 0.86);
}

.ev-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.ev-list li {
  padding: 0.45rem 0.55rem;
  border-radius: 0.75rem;
  border: 1px solid rgba(111, 157, 236, 0.16);
  background: rgba(255, 255, 255, 0.9);
}

.ev-name {
  display: block;
  color: #0f1d40;
}

.ev-meta {
  display: inline-block;
  margin-top: 0.15rem;
  font-size: 0.82rem;
  color: rgba(15, 29, 64, 0.7);
}

.ev-tag {
  display: inline-block;
  margin-left: 0.4rem;
  font-size: 0.8rem;
  padding: 0.1rem 0.5rem;
  border-radius: 999px;
  border: 1px solid rgba(242, 139, 130, 0.55);
  background: rgba(242, 139, 130, 0.12);
}

.analysis-summary {
  margin-top: 0.4rem;
  padding: 0.6rem 0.75rem;
  border-radius: 1rem;
  border: 1px solid rgba(111, 157, 236, 0.16);
  background: rgba(255, 255, 255, 0.9);
  color: rgba(15, 29, 64, 0.9);
}

.meta {
  margin-top: 0.6rem;
  color: rgba(15, 29, 64, 0.82);
}

.muted {
  color: rgba(15, 29, 64, 0.7);
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin-top: 0.4rem;
}

.chip {
  border-radius: 999px;
  padding: 0.3rem 0.7rem;
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(111, 157, 236, 0.2);
  font-size: 0.85rem;
  cursor: pointer;
}

.chip:hover {
  border-color: rgba(111, 157, 236, 0.45);
}


.eyebrow {
  text-transform: uppercase;
  letter-spacing: 0.2em;
  color: rgba(29, 60, 120, 0.7);
  font-size: 0.82rem;
  margin-bottom: 0.4rem;
}

@media (max-width: 640px) {
  .mind-hero,
  .map-shell {
    border-radius: 1.25rem;
  }

  .controls {
    grid-template-columns: 1fr;
  }

  .chart {
    height: 360px;
  }
}
</style>
