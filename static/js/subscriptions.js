const sats = msat => Math.round(msat / 1000).toLocaleString() + ' sats'
const fmtDate = value =>
  new Date(value).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })

const clinkApi = (method, path, wallet, body) =>
  LNbits.api.request(method, path, wallet.adminkey, body)

window.app = Vue.createApp({
  el: '#vue',
  mixins: [windowMixin],
  data() {
    return {
      tab: 'plans',
      wallets: window.g.user.wallets,
      wallet: window.g.user.wallets[0],
      plans: [],
      subscriptions: [],
      planDialog: {
        show: false,
        loading: false,
        name: '',
        amount: null,
        frequencyNumber: 1,
        frequencyUnit: 'month',
        description: ''
      },
      subDialog: {
        show: false,
        loading: false,
        planId: null,
        ndebit: '',
        payerNpub: ''
      }
    }
  },
  methods: {
    sats,
    fmtDate,
    async load() {
      const wallet = this.wallet
      this.plans = (await clinkApi('GET', '/clink/api/v1/plans?wallet=' + wallet.id, wallet)).data
      this.subscriptions = (await clinkApi('GET', '/clink/api/v1/subscriptions?wallet=' + wallet.id, wallet)).data
    },
    openPlanDialog() {
      this.planDialog = {
        show: true,
        loading: false,
        name: '',
        amount: null,
        frequencyNumber: 1,
        frequencyUnit: 'month',
        description: ''
      }
    },
    async createPlan() {
      const d = this.planDialog
      d.loading = true
      try {
        const body = {
          wallet: this.wallet.id,
          name: d.name || null,
          amount_msat: d.amount ? d.amount * 1000 : null,
          frequency_number: d.frequencyNumber,
          frequency_unit: d.frequencyUnit,
          description: d.description || null
        }
        const resp = await clinkApi('POST', '/clink/api/v1/plans', this.wallet, body)
        this.plans.unshift(resp.data)
        d.show = false
        this.$q.notify({ type: 'positive', message: 'Plan created' })
      } catch (e) {
        LNbits.utils.notifyApiError(e)
      } finally {
        d.loading = false
      }
    },
    async togglePlan(p) {
      try {
        await clinkApi('PUT', '/clink/api/v1/plans/' + p.id, this.wallet, { active: p.active })
      } catch (e) {
        p.active = !p.active
        LNbits.utils.notifyApiError(e)
      }
    },
    async deletePlan(p) {
      await clinkApi('DELETE', '/clink/api/v1/plans/' + p.id, this.wallet)
      this.plans = this.plans.filter(x => x.id !== p.id)
    },
    openSubDialog() {
      this.subDialog = { show: true, loading: false, planId: null, ndebit: '', payerNpub: '' }
    },
    async createSubscription() {
      const d = this.subDialog
      d.loading = true
      try {
        const body = {
          wallet: this.wallet.id,
          plan_id: d.planId,
          ndebit: d.ndebit,
          payer_npub: d.payerNpub || null
        }
        const resp = await clinkApi('POST', '/clink/api/v1/subscriptions', this.wallet, body)
        this.subscriptions.unshift(resp.data)
        d.show = false
        this.$q.notify({ type: 'positive', message: 'Subscription created' })
      } catch (e) {
        LNbits.utils.notifyApiError(e)
      } finally {
        d.loading = false
      }
    },
    async renewNow(s) {
      s.loading = true
      try {
        const resp = await clinkApi('POST', '/clink/api/v1/subscriptions/' + s.id + '/renew', this.wallet)
        Object.assign(s, resp.data)
        this.$q.notify({ type: 'positive', message: 'Renewal requested' })
      } catch (e) {
        LNbits.utils.notifyApiError(e)
      } finally {
        s.loading = false
      }
    },
    async toggleSubscription(s) {
      const next = s.state === 'active' ? 'paused' : 'active'
      try {
        const resp = await clinkApi('PUT', '/clink/api/v1/subscriptions/' + s.id, this.wallet, { state: next })
        Object.assign(s, resp.data)
      } catch (e) {
        LNbits.utils.notifyApiError(e)
      }
    },
    async cancelSubscription(s) {
      try {
        const resp = await clinkApi('PUT', '/clink/api/v1/subscriptions/' + s.id, this.wallet, { state: 'cancelled' })
        Object.assign(s, resp.data)
        this.$q.notify({ type: 'warning', message: 'Subscription cancelled' })
      } catch (e) {
        LNbits.utils.notifyApiError(e)
      }
    },
    async deleteSubscription(s) {
      await clinkApi('DELETE', '/clink/api/v1/subscriptions/' + s.id, this.wallet)
      this.subscriptions = this.subscriptions.filter(x => x.id !== s.id)
    }
  },
  watch: {
    wallet() {
      this.load()
    }
  },
  created() {
    this.load()
  }
})
