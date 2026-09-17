<script setup>
// Рекурсивный рендер произвольной структуры из data_json болезни:
// строка / массив строк / массив объектов / вложенный объект.
defineProps({ value: { required: true } })

function isObj(v) {
  return v && typeof v === 'object' && !Array.isArray(v)
}
function humanKey(k) {
  return String(k).replace(/_/g, ' ')
}
</script>

<template>
  <span v-if="value === null || value === undefined" class="muted">—</span>
  <span v-else-if="typeof value === 'string' || typeof value === 'number'">{{ value }}</span>

  <ul v-else-if="Array.isArray(value)">
    <li v-for="(item, i) in value" :key="i">
      <StructValue :value="item" />
    </li>
  </ul>

  <div v-else-if="typeof value === 'object'">
    <div v-for="(v, k) in value" :key="k" style="margin:6px 0">
      <strong>{{ humanKey(k) }}:</strong>
      <StructValue :value="v" />
    </div>
  </div>
</template>
