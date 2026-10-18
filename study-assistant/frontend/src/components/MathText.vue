<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import renderMathInElement from 'katex/contrib/auto-render'

const props = defineProps({
  text: {
    type: String,
    default: '',
  },
})

const rootRef = ref(null)

const render = async () => {
  if (!rootRef.value) return

  // Use textContent to avoid injecting HTML from model output.
  rootRef.value.textContent = props.text || ''

  await nextTick()
  try {
    renderMathInElement(rootRef.value, {
      delimiters: [
        { left: '$$', right: '$$', display: true },
        { left: '$', right: '$', display: false },
      ],
      throwOnError: false,
      strict: 'ignore',
      trust: false,
    })
  } catch {
    // If KaTeX fails, keep plain text
  }
}

watch(
  () => props.text,
  () => {
    render()
  },
  // flush: 'post' 确保 DOM 已挂载后再渲染，避免初次渲染 rootRef 为空导致内容空白
  { immediate: true, flush: 'post' }
)

onMounted(() => {
  // 兜底：确保首次挂载也会渲染
  render()
})

onBeforeUnmount(() => {
  if (rootRef.value) {
    rootRef.value.textContent = ''
  }
})
</script>

<template>
  <div ref="rootRef" class="math-text"></div>
</template>

<style scoped>
.math-text {
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
