<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div class="card budget-card">
      <div class="budget-row">
        <label class="budget-label" for="budget-slider">{{ t('restocking.budgetSlider') }}</label>
        <span class="budget-value">{{ money(budget) }}</span>
      </div>
      <input
        id="budget-slider"
        v-model.number="budget"
        class="budget-slider"
        type="range"
        :min="BUDGET_MIN"
        :max="BUDGET_MAX"
        :step="BUDGET_STEP"
      />
      <div class="budget-scale">
        <span>{{ money(BUDGET_MIN) }}</span>
        <span>{{ money(BUDGET_MAX) }}</span>
      </div>
    </div>

    <div v-if="error" class="error">{{ error }}</div>

    <div v-if="loading && !recommendation" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="recommendation">
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.budget') }}</div>
          <div class="stat-value">{{ money(recommendation.budget) }}</div>
        </div>
        <div class="stat-card info">
          <div class="stat-label">{{ t('restocking.plannedSpend') }}</div>
          <div class="stat-value">{{ money(recommendation.total_cost) }}</div>
        </div>
        <div class="stat-card success">
          <div class="stat-label">{{ t('restocking.remaining') }}</div>
          <div class="stat-value">{{ money(recommendation.remaining_budget) }}</div>
        </div>
        <div class="stat-card warning">
          <div class="stat-label">{{ t('restocking.longestLeadTime') }}</div>
          <div class="stat-value">{{ t('restocking.days', { count: recommendation.lead_time_days }) }}</div>
        </div>
      </div>

      <div v-if="confirmation" class="confirmation" role="status">
        <div class="confirmation-title">{{ t('restocking.confirmTitle') }}</div>
        <dl class="confirmation-details">
          <div>
            <dt>{{ t('restocking.orderNumber') }}</dt>
            <dd>{{ confirmation.order_number }}</dd>
          </div>
          <div>
            <dt>{{ t('restocking.total') }}</dt>
            <dd>{{ money(confirmation.total_cost) }}</dd>
          </div>
          <div>
            <dt>{{ t('restocking.leadTime') }}</dt>
            <dd>{{ t('restocking.days', { count: confirmation.lead_time_days }) }}</dd>
          </div>
          <div>
            <dt>{{ t('restocking.expectedDelivery') }}</dt>
            <dd>{{ formatDate(confirmation.expected_delivery) }}</dd>
          </div>
        </dl>
        <router-link to="/orders" class="confirmation-link">{{ t('restocking.viewOrders') }}</router-link>
      </div>
      <div v-if="orderError" class="error" role="alert">{{ orderError }}</div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.recommendations') }} ({{ recommendation.items.length }})</h3>
          <button
            type="button"
            class="place-order-btn"
            :disabled="!canPlaceOrder"
            @click="placeOrder"
          >
            {{ submitting ? t('restocking.placing') : t('restocking.placeOrder') }}
          </button>
        </div>

        <div v-if="recommendation.items.length === 0" class="empty-state">
          {{ t('restocking.empty') }}
        </div>
        <div v-else class="table-container">
          <table>
            <thead>
              <tr>
                <th>{{ t('restocking.table.sku') }}</th>
                <th>{{ t('restocking.table.name') }}</th>
                <th>{{ t('restocking.table.trend') }}</th>
                <th class="num">{{ t('restocking.table.onHand') }}</th>
                <th class="num">{{ t('restocking.table.forecastedDemand') }}</th>
                <th class="num">{{ t('restocking.table.recommendedQty') }}</th>
                <th class="num">{{ t('restocking.table.unitCost') }}</th>
                <th class="num">{{ t('restocking.table.lineCost') }}</th>
                <th class="num">{{ t('restocking.table.leadTime') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in recommendation.items" :key="item.sku">
                <td><strong>{{ item.sku }}</strong></td>
                <td>{{ translateProductName(item.name) }}</td>
                <td>
                  <span :class="['badge', item.trend]">{{ trendLabel(item.trend) }}</span>
                </td>
                <td class="num">{{ item.quantity_on_hand.toLocaleString() }}</td>
                <td class="num">{{ item.forecasted_demand.toLocaleString() }}</td>
                <td class="num">
                  <strong>{{ item.recommended_quantity.toLocaleString() }}</strong>
                  <span v-if="item.partial" class="badge warning partial-badge">{{ t('restocking.partial') }}</span>
                </td>
                <td class="num">{{ money(item.unit_cost, 2) }}</td>
                <td class="num"><strong>{{ money(item.line_cost) }}</strong></td>
                <td class="num">{{ item.lead_time_days }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'
import { formatCurrency, formatCurrencyWithDecimals } from '../utils/currency'

const BUDGET_MIN = 0
const BUDGET_MAX = 500000
const BUDGET_STEP = 5000
const DEBOUNCE_MS = 250

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency, currentLocale, translateProductName } = useI18n()

    const budget = ref(100000)
    const recommendation = ref(null)
    const loading = ref(false)
    const error = ref(null)

    // True from the moment the slider moves until its recommendations arrive,
    // so the table on screen may not match the slider value.
    const pending = ref(false)

    const submitting = ref(false)
    const orderError = ref(null)
    const confirmation = ref(null)

    let debounceTimer = null
    // Monotonic id: only the most recently issued request may write results,
    // so a slow earlier response can never overwrite a newer one.
    let latestRequestId = 0

    const money = (amount, decimals = 0) =>
      formatCurrencyWithDecimals(amount, currentCurrency.value, decimals)

    const loadRecommendations = async () => {
      const requestId = ++latestRequestId
      const requestedBudget = budget.value
      loading.value = true
      try {
        const data = await api.getRestockRecommendations(requestedBudget)
        if (requestId !== latestRequestId) return // superseded by a newer request
        recommendation.value = data
        error.value = null
        pending.value = false
      } catch (err) {
        if (requestId !== latestRequestId) return
        error.value = t('restocking.loadError') + ': ' + err.message
        pending.value = false
      } finally {
        if (requestId === latestRequestId) loading.value = false
      }
    }

    // Recommendations depend on the budget (and live inventory/demand on the server),
    // so they are re-fetched on every budget change; debounced to avoid a request per slider tick.
    watch(budget, () => {
      pending.value = true
      confirmation.value = null
      orderError.value = null
      clearTimeout(debounceTimer)
      debounceTimer = setTimeout(loadRecommendations, DEBOUNCE_MS)
    })

    // Ordering is blocked while a fetch is pending so we never submit rows that
    // do not match the slider value the user is looking at.
    const canPlaceOrder = computed(() =>
      !!recommendation.value &&
      recommendation.value.items.length > 0 &&
      !submitting.value &&
      !pending.value
    )

    const placeOrder = async () => {
      // Guard against double submit (click handlers can fire before re-render disables the button)
      if (!canPlaceOrder.value) return
      submitting.value = true
      orderError.value = null
      confirmation.value = null
      // Submit exactly what is displayed, using the budget those rows were computed for
      const shown = recommendation.value
      try {
        const order = await api.createRestockOrder({
          items: shown.items.map(i => ({ sku: i.sku, quantity: i.recommended_quantity })),
          budget: shown.budget
        })
        confirmation.value = order
      } catch (err) {
        orderError.value =
          err.response?.data?.detail
            ? (typeof err.response.data.detail === 'string'
              ? err.response.data.detail
              : t('restocking.orderError'))
            : t('restocking.orderError') + ': ' + err.message
      } finally {
        submitting.value = false
      }
    }

    // Parse YYYY-MM-DD as a local date (avoids timezone day shift) and validate before formatting
    const formatDate = (value) => {
      if (!value) return ''
      const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(value)
      const date = m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(value)
      if (isNaN(date.getTime())) return value
      return date.toLocaleDateString(currentLocale.value === 'ja' ? 'ja-JP' : 'en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric'
      })
    }

    const trendLabel = (trend) => {
      const key = `trends.${trend}`
      const label = t(key)
      return label === key ? trend : label
    }

    onMounted(() => {
      pending.value = true
      loadRecommendations()
    })

    onBeforeUnmount(() => {
      clearTimeout(debounceTimer)
      latestRequestId++ // drop any in-flight response after leaving the page
    })

    return {
      t,
      BUDGET_MIN,
      BUDGET_MAX,
      BUDGET_STEP,
      budget,
      recommendation,
      loading,
      error,
      submitting,
      orderError,
      confirmation,
      canPlaceOrder,
      placeOrder,
      money,
      formatDate,
      trendLabel,
      translateProductName
    }
  }
}
</script>

<style scoped>
.budget-card {
  margin-bottom: 1.5rem;
}

.budget-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 0.75rem;
}

.budget-label {
  color: #64748b;
  font-size: 0.875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.budget-value {
  font-size: 1.75rem;
  font-weight: 700;
  color: #0f172a;
  letter-spacing: -0.025em;
}

.budget-slider {
  width: 100%;
  accent-color: #2563eb;
  cursor: pointer;
}

.budget-scale {
  display: flex;
  justify-content: space-between;
  color: #64748b;
  font-size: 0.75rem;
  margin-top: 0.25rem;
}

.num {
  text-align: right;
}

.partial-badge {
  margin-left: 0.5rem;
}

.place-order-btn {
  background: #2563eb;
  color: white;
  border: none;
  border-radius: 6px;
  padding: 0.5rem 1.25rem;
  font-size: 0.875rem;
  font-weight: 600;
  cursor: pointer;
  transition: background-color 0.15s ease;
}

.place-order-btn:hover:not(:disabled) {
  background: #1d4ed8;
}

.place-order-btn:disabled {
  background: #cbd5e1;
  color: #64748b;
  cursor: not-allowed;
}

.empty-state {
  text-align: center;
  padding: 2rem;
  color: #64748b;
  font-size: 0.938rem;
}

.confirmation {
  background: #ecfdf5;
  border: 1px solid #a7f3d0;
  color: #065f46;
  border-radius: 8px;
  padding: 1rem;
  margin-bottom: 1.25rem;
}

.confirmation-title {
  font-weight: 700;
  margin-bottom: 0.75rem;
}

.confirmation-details {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 0.75rem;
  margin-bottom: 0.75rem;
}

.confirmation-details dt {
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #047857;
}

.confirmation-details dd {
  font-weight: 600;
  color: #0f172a;
}

.confirmation-link {
  color: #2563eb;
  font-weight: 600;
  font-size: 0.875rem;
}
</style>
