<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api/client'

const items = ref([])
const loading = ref(true)
const error = ref('')
const q = ref('')
const drugClass = ref('')
const animal = ref('')
const origin = ref('')
const limit = 50
const offset = ref(0)
const hasMore = ref(false)

async function load(reset = true) {
  if (reset) offset.value = 0
  loading.value = true
  error.value = ''
  try {
    const rows = await api.preparations({
      q: q.value.trim(),
      drug_class: drugClass.value.trim(),
      animal: animal.value.trim(),
      origin: origin.value,
      limit,
      offset: offset.value,
    })
    items.value = reset ? rows : items.value.concat(rows)
    hasMore.value = rows.length === limit
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function loadMore() {
  offset.value += limit
  load(false)
}

onMounted(() => load())
</script>

<template>
  <div>
    <h1>Препараты</h1>
    <p class="muted">
      Фильтры «класс» и «вид животного» заполнены только у препаратов каталога ВИК
      (origin = drugs, 79 записей). У препаратов гос.реестра (galen) этих полей нет —
      их ищите текстом.
    </p>
    <div class="filters">
      <input v-model="q" placeholder="Поиск по названию / инструкции…" @keyup.enter="load()" />
      <input v-model="drugClass" placeholder="Класс (напр. антибактериальные)" @keyup.enter="load()" />
      <input v-model="animal" placeholder="Вид (напр. свиньи)" @keyup.enter="load()" />
      <select v-model="origin" @change="load()">
        <option value="">Все источники</option>
        <option value="drugs">Каталог ВИК (drugs)</option>
        <option value="galen">Гос.реестр (galen)</option>
      </select>
      <button class="btn" @click="load()">Применить</button>
    </div>

    <div v-if="loading && !items.length" class="state">Загрузка…</div>
    <div v-else-if="error" class="state">Ошибка: {{ error }}</div>
    <div v-else-if="!items.length" class="state">Ничего не найдено.</div>

    <template v-else>
      <div class="grid">
        <router-link
          v-for="p in items"
          :key="p.id"
          class="card"
          :to="{ name: 'preparation', params: { id: p.id } }"
        >
          <h3>{{ p.trade_name || '—' }}</h3>
          <div class="meta">
            <span class="badge">{{ p.origin }}</span>
            <span v-if="p.drug_class">{{ p.drug_class }}</span>
          </div>
          <div class="meta" v-if="p.manufacturer" style="margin-top:4px">{{ p.manufacturer }}</div>
        </router-link>
      </div>
      <div style="text-align:center;margin-top:16px">
        <button v-if="hasMore" class="btn secondary" :disabled="loading" @click="loadMore">
          {{ loading ? 'Загрузка…' : 'Показать ещё' }}
        </button>
      </div>
    </template>
  </div>
</template>
