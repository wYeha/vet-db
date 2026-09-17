<script setup>
defineProps({
  items: { type: Array, default: () => [] },
  activeIndex: { type: Number, default: -1 },
})
const emit = defineEmits(['go'])
</script>

<template>
  <div class="panel toc">
    <strong>Оглавление</strong>
    <div v-if="!items.length" class="muted" style="margin-top:8px">Пусто</div>
    <a
      v-for="(it, i) in items"
      :key="i"
      class="toc-item"
      :class="[
        'lvl-' + (it.level || 1),
        { active: it.page_index != null && it.page_index === activeIndex,
          'no-page': it.page_index == null },
      ]"
      @click.prevent="it.page_index != null && emit('go', it.page_index)"
    >{{ it.title }}
      <span v-if="it.page_index != null" class="muted">· {{ it.page_index + 1 }}</span>
      <span v-else-if="it.origin === 'curated'" class="muted" title="курированное оглавление">· глава</span>
    </a>
  </div>
</template>
