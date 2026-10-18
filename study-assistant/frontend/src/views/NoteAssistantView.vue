<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:3000'

const auth = useAuthStore()
const router = useRouter()

const uploadStatus = ref('idle')
const errorMessage = ref('')
const transcript = ref('')
const summary = ref([])
const actionItems = ref([])

const entryId = ref(0)
const pollTimer = ref(null)

const fileInputRef = ref(null)

const recording = ref(false)
const sessionId = ref('')
const mediaRecorder = ref(null)
const lastChunkText = ref('')
const hasAudioInput = ref(false)

const canUseRecording = computed(() => Boolean(navigator?.mediaDevices?.getUserMedia))

const ensureAuth = async () => {
  if (!auth.token) {
    router.push('/login')
    return false
  }
  try {
    await auth.fetchProfile()
    return true
  } catch (e) {
    router.push('/login')
    return false
  }
}

const resetView = () => {
  transcript.value = ''
  summary.value = []
  actionItems.value = []
  errorMessage.value = ''
  lastChunkText.value = ''
  hasAudioInput.value = false
  entryId.value = 0
  try {
    if (pollTimer.value) clearTimeout(pollTimer.value)
  } catch {
    // ignore
  }
  pollTimer.value = null
}

const hasTranscript = computed(() => Boolean((transcript.value || '').trim()))

const loadExample = async () => {
  if (!(await ensureAuth())) return
  resetView()
  try {
    const res = await fetch(`${API_BASE_URL}/api/note/session`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${auth.token}` },
      body: JSON.stringify({ title: '示例课堂：二次函数', subject: '数学' }),
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data.message || '示例加载失败')
    entryId.value = data.entry_id
    transcript.value = '【示例课堂】今天学习二次函数 y=ax²+bx+c，其中 a 不等于 0。当 a 大于 0 时，抛物线开口向上；当 a 小于 0 时，开口向下。对称轴为 x=-b/(2a)。例如 y=x²-4x+3，可以配方为 y=(x-2)²-1，所以顶点为 (2,-1)，与 x 轴交点为 (1,0) 和 (3,0)。课后请练习配方法，画出函数图像，并比较不同参数对开口和顶点位置的影响。'
    uploadStatus.value = 'done'
  } catch (e) { errorMessage.value = e.message }
}

const fetchNoteEntryDetail = async (id) => {
  const res = await fetch(`${API_BASE_URL}/api/note/entries/${id}`, {
    headers: { Authorization: `Bearer ${auth.token}` },
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    if (res.status === 401) {
      auth.logout()
      router.push('/login')
      throw new Error('登录已过期，请重新登录')
    }
    throw new Error(data?.message || '获取处理结果失败')
  }
  return data
}

const startPollingEntry = async (id) => {
  try {
    if (pollTimer.value) clearTimeout(pollTimer.value)
  } catch {
    // ignore
  }

  const tick = async () => {
    if (!id) return
    try {
      const data = await fetchNoteEntryDetail(id)

      if (typeof data?.transcript === 'string' && data.transcript) {
        transcript.value = data.transcript
      }
      summary.value = data?.summary?.summary_points || []
      actionItems.value = Array.isArray(data?.tasks) ? data.tasks : []

      const s = String(data?.status || '')
      if (s === 'done') {
        uploadStatus.value = 'done'
        return
      }
      if (s === 'summary_failed') {
        uploadStatus.value = 'done'
        if (!summary.value.length) errorMessage.value = '摘要失败（但转写已完成）'
        return
      }
      if (s === 'transcribe_failed') {
        uploadStatus.value = 'failed'
        errorMessage.value = '转写失败'
        return
      }
      uploadStatus.value = 'summarizing'
    } catch (e) {
      errorMessage.value = e?.message || '获取处理结果失败'
    }
    pollTimer.value = setTimeout(tick, 1200)
  }

  pollTimer.value = setTimeout(tick, 600)
}

const handleFileUpload = async (event) => {
  const file = event.target.files?.[0]
  if (!file) return
  if (!(await ensureAuth())) return

  resetView()
  uploadStatus.value = 'processing'

  try {
    const form = new FormData()
    form.append('audio', file)

    const response = await fetch(`${API_BASE_URL}/api/note/entries`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${auth.token}` },
      body: form,
    })
    const data = await response.json().catch(() => ({}))
    if (!response.ok) {
      if (response.status === 401) {
        auth.logout()
        router.push('/login')
        throw new Error('登录已过期，请重新登录')
      }
      throw new Error(data?.message || '转录失败')
    }

    const serverStatus = String(data?.status || '')
    entryId.value = Number(data?.id || 0)
    transcript.value = data?.transcript || ''
    summary.value = []
    actionItems.value = []

    if (serverStatus === 'transcribe_failed') {
      uploadStatus.value = 'failed'
      errorMessage.value = data?.message || '转写失败'
      return
    }

    uploadStatus.value = 'done'
  } catch (e) {
    uploadStatus.value = 'failed'
    errorMessage.value = e?.message || '转录失败'
  } finally {
    // Allow uploading the same file again (otherwise change event may not fire)
    try {
      if (event?.target) event.target.value = ''
      if (fileInputRef.value) fileInputRef.value.value = ''
    } catch {
      // ignore
    }
  }
}


const startRecording = async () => {
  if (!(await ensureAuth())) return
  if (!canUseRecording.value) {
    errorMessage.value = '当前浏览器不支持录音 API'
    return
  }
  if (recording.value) return

  resetView()
  uploadStatus.value = 'recording'

  const createRes = await fetch(`${API_BASE_URL}/api/note/session`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${auth.token}`,
    },
    body: JSON.stringify({}),
  })
  const createData = await createRes.json().catch(() => ({}))
  if (!createRes.ok) {
    throw new Error(createData?.message || '创建录音会话失败')
  }
  sessionId.value = createData.session_id
  entryId.value = Number(createData.entry_id || 0)

  const pickRecordingMimeType = () => {
    const candidates = [
      'audio/webm;codecs=opus',
      'audio/webm',
      'audio/ogg;codecs=opus',
      'audio/ogg',
    ]
    for (const t of candidates) {
      try {
        if (window.MediaRecorder?.isTypeSupported?.(t)) return t
      } catch {
        // ignore
      }
    }
    return ''
  }

  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  })

  const mimeType = pickRecordingMimeType()
  const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream)
  mediaRecorder.value = recorder
  recording.value = true

  recorder.ondataavailable = async (ev) => {
    if (!ev.data || ev.data.size === 0) return
    if (!sessionId.value) return
    hasAudioInput.value = true
    try {
      const fd = new FormData()
      const ext = String(mimeType || '').includes('ogg') ? 'ogg' : 'webm'
      fd.append('audio', ev.data, `chunk_${Date.now()}.${ext}`)
      const res = await fetch(`${API_BASE_URL}/api/note/session/${sessionId.value}/chunk`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${auth.token}` },
        body: fd,
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(data?.message || '分片上传失败')
      }
      if (data?.ok === false && data?.message) {
        if (data?.message) errorMessage.value = data.message
        return
      }
    } catch (e) {
      errorMessage.value = e?.message || '分片上传失败'
    }
  }

  recorder.start(2500)
}

const stopRecordingAndTranscribe = async () => {
  if (!recording.value) return
  uploadStatus.value = 'processing'
  recording.value = false

  const recorder = mediaRecorder.value
  try {
    recorder?.stop()
  } catch {
    // ignore
  }
  try {
    const tracks = recorder?.stream?.getTracks?.() || []
    tracks.forEach((t) => t.stop())
  } catch {
    // ignore
  }

  if (!hasAudioInput.value) {
    uploadStatus.value = 'idle'
    errorMessage.value = '未检测到音频输入：请先录到有效声音再进行 AI 总结。'
    sessionId.value = ''
    return
  }

  try {
    const res = await fetch(`${API_BASE_URL}/api/note/session/${sessionId.value}/finalize`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${auth.token}` },
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data?.message || '转写失败')
    const serverStatus = String(data?.status || '')
    entryId.value = Number(data?.id || entryId.value || 0)
    transcript.value = data?.transcript || transcript.value
    summary.value = []
    actionItems.value = []

    if (serverStatus === 'transcribe_failed') {
      uploadStatus.value = 'failed'
      errorMessage.value = data?.message || '转写失败'
      return
    }

    uploadStatus.value = 'done'
  } catch (e) {
    uploadStatus.value = 'failed'
    errorMessage.value = e?.message || '转写失败'
  }
}

const recordButtonText = computed(() => (recording.value ? '停止录音' : '开始录音'))

const aiSummaryButtonText = computed(() => {
  if (uploadStatus.value === 'summarizing') return 'AI总结处理中...'
  return 'AI总结'
})

const handleRecordToggle = async () => {
  errorMessage.value = ''
  if (recording.value) {
    await stopRecordingAndTranscribe()
    return
  }
  transcript.value = ''
  await startRecording()
}

const handleAiSummary = async () => {
  errorMessage.value = ''
  if (!entryId.value || !hasTranscript.value) return
  if (!(await ensureAuth())) return
  if (recording.value) return
  if (uploadStatus.value === 'processing' || uploadStatus.value === 'summarizing') return

  uploadStatus.value = 'summarizing'
  summary.value = []
  actionItems.value = []

  const res = await fetch(`${API_BASE_URL}/api/note/entries/${entryId.value}/summarize`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${auth.token}`,
    },
    body: JSON.stringify({ transcript: transcript.value }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    uploadStatus.value = 'done'
    throw new Error(data?.message || 'AI 总结失败')
  }
  startPollingEntry(entryId.value)
}

const transcriptPlaceholderText = computed(() => {
  if (recording.value) return '停止录音后开始转录'
  if (uploadStatus.value === 'processing') return '正在转录...'
  return '上传音频或录音后将在这里显示逐字稿，并可直接修改。'
})

onBeforeUnmount(() => {
  try {
    mediaRecorder.value?.stream?.getTracks?.().forEach((t) => t.stop())
  } catch {
    // ignore
  }
  try {
    if (pollTimer.value) clearTimeout(pollTimer.value)
  } catch {
    // ignore
  }
})
</script>

<template>
  <section class="note-hero">
    <div>
      <p class="eyebrow">Note Assistant</p>
      <h1>录音 · 停止后转写 · 一键总结</h1>
      <p>
        先录下课堂内容，停止录音后完成转写；确认/编辑逐字稿后，再点击 AI 总结生成要点与待办。
      </p>
      <div class="hero-actions">
        <RouterLink class="secondary" to="/">返回首页</RouterLink>
        <a class="primary" href="#workspace">开始记录</a>
      </div>
    </div>
    <div class="mini-cards">
      <article>
        <p class="label">转写方式</p>
        <p class="value">停止后</p>
        <small>合并录音再转写</small>
      </article>
      <article>
        <p class="label">总结触发</p>
        <p class="value">手动</p>
        <small>有文本才可总结</small>
      </article>
    </div>
  </section>

  <section class="workspace" id="workspace">
    <article class="capture">
      <header>
        <div>
          <p class="eyebrow">录音 / 上传</p>
          <h2>获取课堂原始素材</h2>
        </div>
        <span class="status" :class="uploadStatus">{{ uploadStatus }}</span>
      </header>
      <label class="file-input">
        <input ref="fileInputRef" type="file" accept="audio/*" @change="handleFileUpload" />
        <span>拖拽音频或点击上传（mp3 / wav）</span>
      </label>
      <div class="record-actions">
        <button type="button" class="secondary" :disabled="recording || uploadStatus === 'processing' || uploadStatus === 'summarizing'" @click="loadExample">试用示例课堂</button>
        <button
          type="button"
          class="secondary"
          :disabled="!canUseRecording || uploadStatus === 'processing' || uploadStatus === 'summarizing'"
          @click="handleRecordToggle"
        >
          {{ recordButtonText }}
        </button>
        <button
          type="button"
          class="primary"
          :disabled="!hasTranscript || uploadStatus === 'processing' || uploadStatus === 'summarizing' || recording"
          @click="handleAiSummary"
        >
          {{ aiSummaryButtonText }}
        </button>
      </div>
      <p class="hint">
        小提示：录音会先上传分片，停止录音后再统一转写；也可以上传已有的课堂音频。
      </p>
      <p v-if="errorMessage" class="placeholder">{{ errorMessage }}</p>
    </article>
    <article class="transcript">
      <header>
        <div>
          <p class="eyebrow">转写文本</p>
          <h2>{{ transcript ? '最新课堂记录' : '等待上传音频' }}</h2>
        </div>
      </header>
      <textarea
        v-if="transcript"
        v-model="transcript"
        class="transcript-editor"
        rows="12"
        spellcheck="false"
      />
      <p v-else class="placeholder">{{ transcriptPlaceholderText }}</p>
    </article>
  </section>

  <section class="insights">
    <article>
      <header>
        <p class="eyebrow">关键要点</p>
        <h3>智能摘要</h3>
      </header>
      <ul>
        <li v-for="point in summary" :key="point">{{ point }}</li>
      </ul>
      <p v-if="!summary.length" class="placeholder">转写完成后点击 AI 总结生成要点。</p>
    </article>
    <article>
      <header>
        <p class="eyebrow">复习待办</p>
        <h3>任务追踪</h3>
      </header>
      <ul class="actions">
        <li v-for="item in actionItems" :key="item.id">
          <label>
            <input type="checkbox" v-model="item.done" />
            <span :class="{ done: item.done }">{{ item.text }}</span>
          </label>
        </li>
      </ul>
    </article>
  </section>
</template>

<style scoped>
.note-hero,
.workspace,
.insights {
  background: rgba(255, 255, 255, 0.95);
  border-radius: 2rem;
  padding: clamp(1.5rem, 4vw, 3rem);
  box-shadow: 0 25px 60px rgba(21, 47, 89, 0.08);
  margin-bottom: 2rem;
}

.note-hero {
  display: flex;
  flex-wrap: wrap;
  gap: 2rem;
  align-items: center;
}

.mini-cards {
  display: grid;
  gap: 1rem;
}

.mini-cards article {
  border-radius: 1.5rem;
  padding: 1.2rem;
  background: rgba(248, 251, 255, 0.9);
  border: 1px solid rgba(111, 157, 236, 0.2);
}

.eyebrow {
  text-transform: uppercase;
  letter-spacing: 0.2em;
  color: rgba(29, 60, 120, 0.7);
  font-size: 0.82rem;
  margin-bottom: 0.4rem;
}

.hero-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.8rem;
  margin-top: 1.5rem;
}

.primary,
.secondary {
  border-radius: 999px;
  padding: 0.8rem 1.6rem;
  font-weight: 600;
  text-align: center;
}

.primary {
  background: linear-gradient(120deg, #6f9dec, #8bd4c8);
  color: #fff;
}

.secondary {
  border: 1px solid rgba(17, 62, 125, 0.25);
  color: #0c2b53;
}

.workspace {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 1.5rem;
}

.workspace article {
  background: rgba(248, 251, 255, 0.9);
  border-radius: 1.5rem;
  padding: 1.5rem;
  border: 1px solid rgba(111, 157, 236, 0.2);
}

.status {
  text-transform: uppercase;
  font-size: 0.75rem;
  padding: 0.2rem 0.8rem;
  border-radius: 999px;
  border: 1px solid rgba(15, 39, 88, 0.2);
}

.status.processing {
  color: #b97b0d;
  border-color: #f7c66a;
}

.status.done {
  color: #0c7550;
  border-color: #8bd4c8;
}

.file-input {
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px dashed rgba(15, 39, 88, 0.3);
  border-radius: 1rem;
  padding: 1.3rem;
  margin: 1rem 0;
  cursor: pointer;
  min-height: 120px;
}

.record-actions {
  display: flex;
  gap: 0.8rem;
  flex-wrap: wrap;
  margin-top: 1rem;
}

.record-actions button {
  border-radius: 999px;
  padding: 0.7rem 1.2rem;
  font-weight: 600;
  border: 1px solid rgba(17, 62, 125, 0.25);
}

.record-actions button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.file-input input {
  display: none;
}

.presets {
  display: flex;
  gap: 0.6rem;
  flex-wrap: wrap;
}

.presets button {
  border-radius: 999px;
  padding: 0.4rem 1rem;
  border: 1px solid rgba(17, 62, 125, 0.25);
  background: rgba(255, 255, 255, 0.8);
  cursor: pointer;
}

.hint {
  font-size: 0.9rem;
  color: rgba(15, 39, 88, 0.6);
  margin-top: 1rem;
}

.transcript-editor {
  white-space: pre-wrap;
  font-family: 'Fira Code', 'SFMono-Regular', Consolas, monospace;
  background: rgba(26, 39, 66, 0.85);
  color: #e8f0ff;
  padding: 1rem;
  border-radius: 1rem;
  line-height: 1.55;
  max-height: 360px;
  overflow-y: auto;
  width: 100%;
  border: 0;
  resize: vertical;
}

.placeholder {
  color: rgba(15, 39, 88, 0.6);
}

.insights {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 1.5rem;
}

.insights article {
  border-radius: 1.5rem;
  padding: 1.5rem;
  border: 1px solid rgba(111, 157, 236, 0.16);
}

.insights ul {
  list-style: none;
  padding: 0;
  margin: 1rem 0 0;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.insights li::before {
  content: '• ';
  color: #6f9dec;
}

.actions {
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 0.8rem;
}

.actions label {
  display: flex;
  gap: 0.6rem;
  align-items: center;
}

.actions input[type='checkbox'] {
  width: 1rem;
  height: 1rem;
}

.actions .done {
  text-decoration: line-through;
  color: rgba(15, 39, 88, 0.4);
}

@media (max-width: 640px) {
  .note-hero,
  .workspace,
  .insights {
    border-radius: 1.25rem;
  }
}
</style>
