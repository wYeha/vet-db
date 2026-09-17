<script setup>
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'

const props = defineProps({
  source: { type: String, default: '' },
  // подсветка терма на клиенте (для перехода из поиска внутри книги)
  highlight: { type: String, default: '' },
})

const md = new MarkdownIt({ html: false, linkify: true, breaks: false })

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

const rendered = computed(() => {
  let html = md.render(props.source || '')
  const term = (props.highlight || '').trim()
  if (term.length >= 2) {
    // подсветка только в текстовых узлах (грубо: вне тегов)
    const re = new RegExp(`(${escapeRe(term)})`, 'gi')
    html = html.replace(/>([^<]+)</g, (m, text) => '>' + text.replace(re, '<mark>$1</mark>') + '<')
  }
  return html
})
</script>

<template>
  <div class="markdown" v-html="rendered"></div>
</template>
