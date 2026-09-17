<script setup>
import { ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api/client'

const route = useRoute()
const router = useRouter()

const q = ref(route.query.q || '')
const scope = ref(route.query.scope || 'all')
const hits = ref([])
const loading = ref(false)
const error = ref('')
const searched = ref(false)

async function run() {
  const query = q.value.trim()
  if (!query) return
  loading.value = true
  error.value = ''
  searched.value = true
  router.replace({ name: 'search', query: { q: query, scope: scope.value } })
  try {
    hits.value = await api.search({ q: query, scope: scope.value, limit: 50 })
  } catch (e) {
    error.value = e.message
    hits.value = []
  } finally {
    loading.value = false
  }
}

const pages = computed(() => hits.value.filter((h) => h.type === 'page'))
const preps = computed(() => hits.value.filter((h) => h.type === 'preparation'))
const diseases = computed(() => hits.value.filter((h) => h.type === 'disease'))

watch(() => route.query.q, (v) => { if (v && v !== q.value) { q.value = v; run() } })

if (q.value) run()
</script>

<template>
  <div>
    <h1>Поиск</h1>
    <div class="filters">
      <input v-model="q" style="min-width:320px" placeholder="Запрос…" @keyup.enter="run" />
      <select v-model="scope" @change="run">
        <option value="all">Везде</option>
        <option value="books">Книги</option>
        <option value="pharma">Препараты</option>
        <option value="diseases">Болезни</option>
      </select>
      <button class="btn" @click="run">Найти</button>
    </div>

    <div v-if="loading" class="state">Ищу…</div>
    <div v-else-if="error" class="state">Ошибка: {{ error }}</div>
    <div v-else-if="searched && !hits.length" class="state">Ничего не найдено.</div>

    <template v-else>
      <section v-if="pages.length">
        <h2 class="section-title">Страницы книг ({{ pages.length }})</h2>
        <div class="panel">
          <div v-for="(h, i) in pages" :key="'p' + i" class="hit">
            <router-link :to="{ name: 'reader', params: { id: h.source_id }, query: { page: h.page_index } }">
              {{ h.source_title }} → стр. {{ h.page_index + 1 }}
            </router-link>
            <div class="snippet" v-html="h.snippet"></div>
          </div>
        </div>
      </section>

      <section v-if="preps.length">
        <h2 class="section-title">Препараты ({{ preps.length }})</h2>
        <div class="panel">
          <div v-for="(h, i) in preps" :key="'r' + i" class="hit">
            <router-link :to="{ name: 'preparation', params: { id: h.ref_id } }">{{ h.title }}</router-link>
            <div class="snippet" v-html="h.snippet"></div>
          </div>
        </div>
      </section>

      <section v-if="diseases.length">
        <h2 class="section-title">Болезни ({{ diseases.length }})</h2>
        <div class="panel">
          <div v-for="(h, i) in diseases" :key="'d' + i" class="hit">
            <router-link :to="{ name: 'disease', params: { id: h.ref_id } }">{{ h.title }}</router-link>
            <div class="snippet" v-html="h.snippet"></div>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>
