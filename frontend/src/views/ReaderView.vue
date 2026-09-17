<script setup>
import { ref, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api/client'
import TocTree from '../components/TocTree.vue'
import MarkdownView from '../components/MarkdownView.vue'

const props = defineProps({ id: { type: [String, Number], required: true } })
const route = useRoute()

const source = ref(null)
const toc = ref([])
const pageIndex = ref(0)
const markdown = ref('')
const numPages = ref(0)
const loading = ref(true)
const error = ref('')

// поиск внутри книги (Ctrl+F)
const bookQuery = ref('')
const bookResults = ref([])
const searching = ref(false)
const highlight = ref('')

async function loadSource() {
  loading.value = true
  error.value = ''
  try {
    const [s, t] = await Promise.all([api.source(props.id), api.toc(props.id)])
    source.value = s
    toc.value = t
    numPages.value = s.num_pages || 0
    const initial = Number(route.query.page)
    await loadPage(Number.isInteger(initial) && initial >= 0 ? initial : 0)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function loadPage(idx) {
  if (idx < 0 || (numPages.value && idx >= numPages.value)) return
  try {
    const p = await api.page(props.id, idx)
    markdown.value = p.markdown
    pageIndex.value = p.page_index
    numPages.value = p.num_pages || numPages.value
  } catch (e) {
    error.value = e.message
  }
}

async function runBookSearch() {
  const q = bookQuery.value.trim()
  if (!q) { bookResults.value = []; return }
  searching.value = true
  try {
    bookResults.value = await api.search({ q, scope: 'books', source_id: props.id, limit: 50 })
  } catch (e) {
    error.value = e.message
  } finally {
    searching.value = false
  }
}

function gotoHit(hit) {
  highlight.value = bookQuery.value.trim()
  loadPage(hit.page_index)
}

function goTocPage(idx) {
  highlight.value = ''
  loadPage(idx)
}

function prev() { highlight.value = ''; loadPage(pageIndex.value - 1) }
function next() { highlight.value = ''; loadPage(pageIndex.value + 1) }

function openPdf() {
  window.open(api.pdfUrl(props.id), '_blank')
}

onMounted(loadSource)
watch(() => props.id, loadSource)
</script>

<template>
  <div v-if="loading" class="state">Загрузка…</div>
  <div v-else-if="error" class="state">Ошибка: {{ error }}</div>
  <div v-else>
    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px">
      <router-link to="/">← К источникам</router-link>
      <h1 style="margin:0;font-size:20px">{{ source.title }}</h1>
      <button v-if="source.has_pdf" class="btn secondary" @click="openPdf">Оригинал (PDF)</button>
      <a v-else-if="source.source_url" :href="source.source_url" target="_blank" class="btn secondary">Источник</a>
    </div>

    <div class="reader">
      <div>
        <div class="in-book-search">
          <input
            v-model="bookQuery"
            placeholder="Поиск внутри книги (Ctrl+F)…"
            @keyup.enter="runBookSearch"
          />
          <button class="btn" @click="runBookSearch">↵</button>
        </div>
        <div v-if="searching" class="muted">Ищу…</div>
        <div v-else-if="bookResults.length" class="panel results-list">
          <div class="muted" style="margin-bottom:4px">Найдено: {{ bookResults.length }}</div>
          <div
            v-for="(h, i) in bookResults"
            :key="i"
            class="result-line"
            @click="gotoHit(h)"
          >
            стр. {{ h.page_index + 1 }} <span class="snippet" v-html="h.snippet"></span>
          </div>
        </div>
        <TocTree :items="toc" :active-index="pageIndex" @go="goTocPage" />
      </div>

      <div>
        <div class="pager">
          <button class="btn secondary" :disabled="pageIndex <= 0" @click="prev">‹ Назад</button>
          <span>стр.
            <input
              type="number"
              :value="pageIndex + 1"
              min="1"
              :max="numPages"
              @change="(e) => { highlight=''; loadPage(Number(e.target.value) - 1) }"
            /> / {{ numPages }}
          </span>
          <button class="btn secondary" :disabled="pageIndex >= numPages - 1" @click="next">Вперёд ›</button>
        </div>
        <MarkdownView :source="markdown" :highlight="highlight" />
      </div>
    </div>
  </div>
</template>
