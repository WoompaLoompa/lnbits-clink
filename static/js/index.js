const sats = msat => Math.round(msat / 1000).toLocaleString() + ' sats'

const clinkApi = (method, path, wallet, body) =>
  LNbits.api.request(method, path, wallet.adminkey, body)

window.app = Vue.createApp({
  el: '#vue',
  mixins: [windowMixin],
  components: {
    QrcodeVue: QrcodeVue.default
  },
  data() {
    return {
      tab: 'offers',
      wallets: this.g.user.wallets,
      wallet: this.g.user.wallets[0],
      offers: [],
      debits: [],
      relays: [],
      offerDialog: { show: false, loading: false, name: '', amount: null, description: '', relay: '' },
      debitDialog: {
        show: false,
        loading: false,
        amount: null,
        budget: null,
        frequencyNumber: 1,
        frequencyUnit: 'month',
        rules: '',
        active: true
      },
      relayDialog: { show: false, loading: false, url: '', enabled: true },
      qrDialog: { show: false, value: '' }
    }
  },
  methods: {
    sats,
    copy(text) {
      LNbits.utils.copyText(text)
      this.$q.notify({ type: 'positive', message: 'Copied' })
    },
    showQr(value) {
      this.qrDialog.value = value
      this.qrDialog.show = true
    },
    async load() {
      const wallet = this.wallet
      this.offers = (await clinkApi('GET', '/clink/api/v1/offers?wallet=' + wallet.id, wallet)).data
      this.debits = (await clinkApi('GET', '/clink/api/v1/debits?wallet=' + wallet.id, wallet)).data
      this.relays = (await clinkApi('GET', '/clink/api/v1/relays', wallet)).data
    },
    openOfferDialog() {
      this.offerDialog = { show: true, loading: false, name: '', amount: null, description: '', relay: '' }
    },
    async createOffer() {
      const d = this.offerDialog
      d.loading = true
      try {
        const body = {
          wallet: this.wallet.id,
          name: d.name || null,
          amount_msat: d.amount ? d.amount * 1000 : null,
          description: d.description || null,
          relay: d.relay || null
        }
        const resp = await clinkApi('POST', '/clink/api/v1/offers', this.wallet, body)
        this.offers.unshift(resp.data)
        d.show = false
        this.$q.notify({ type: 'positive', message: 'Offer created' })
      } catch (e) {
        LNbits.utils.notifyApiError(e)
      } finally {
        d.loading = false
      }
    },
    async toggleOffer(o) {
      try {
        await clinkApi('PUT', '/clink/api/v1/offers/' + o.id, this.wallet, { active: o.active })
      } catch (e) {
        o.active = !o.active
        LNbits.utils.notifyApiError(e)
      }
    },
    async deleteOffer(o) {
      await clinkApi('DELETE', '/clink/api/v1/offers/' + o.id, this.wallet)
      this.offers = this.offers.filter(x => x.id !== o.id)
    },
    openDebitDialog() {
      this.debitDialog = {
        show: true,
        loading: false,
        amount: null,
        budget: null,
        frequencyNumber: 1,
        frequencyUnit: 'month',
        rules: '',
        active: true
      }
    },
    async createDebit() {
      const d = this.debitDialog
      d.loading = true
      try {
        let rules = d.rules || null
        if (rules && !rules.trim().startsWith('{')) {
          rules = JSON.stringify({ allowed_pubkeys: rules.split(',').map(x => x.trim()) })
        }
        const body = {
          wallet: this.wallet.id,
          amount_msat: d.amount ? d.amount * 1000 : null,
          budget_msat: d.budget ? d.budget * 1000 : null,
          frequency_number: d.frequencyNumber,
          frequency_unit: d.frequencyUnit,
          rules,
          state: d.active ? 'active' : 'pending'
        }
        const resp = await clinkApi('POST', '/clink/api/v1/debits', this.wallet, body)
        this.debits.unshift(resp.data)
        d.show = false
        this.$q.notify({ type: 'positive', message: 'Debit pointer created' })
      } catch (e) {
        LNbits.utils.notifyApiError(e)
      } finally {
        d.loading = false
      }
    },
    async toggleDebit(d) {
      const next = d.state === 'active' ? 'pending' : 'active'
      try {
        const resp = await clinkApi('PUT', '/clink/api/v1/debits/' + d.id, this.wallet, { state: next })
        Object.assign(d, resp.data)
      } catch (e) {
        LNbits.utils.notifyApiError(e)
      }
    },
    async deleteDebit(d) {
      await clinkApi('DELETE', '/clink/api/v1/debits/' + d.id, this.wallet)
      this.debits = this.debits.filter(x => x.id !== d.id)
    },
    openRelayDialog() {
      this.relayDialog = { show: true, loading: false, url: '', enabled: true }
    },
    async createRelay() {
      const d = this.relayDialog
      d.loading = true
      try {
        const resp = await clinkApi('POST', '/clink/api/v1/relays', this.wallet, { url: d.url, enabled: d.enabled })
        this.relays.push(resp.data)
        d.show = false
        this.$q.notify({ type: 'positive', message: 'Relay added' })
      } catch (e) {
        LNbits.utils.notifyApiError(e)
      } finally {
        d.loading = false
      }
    },
    async toggleRelay(r) {
      try {
        const resp = await clinkApi('PUT', '/clink/api/v1/relays/' + r.id, this.wallet, { enabled: r.enabled })
        Object.assign(r, resp.data)
      } catch (e) {
        r.enabled = !r.enabled
        LNbits.utils.notifyApiError(e)
      }
    },
    async deleteRelay(r) {
      await clinkApi('DELETE', '/clink/api/v1/relays/' + r.id, this.wallet)
      this.relays = this.relays.filter(x => x.id !== r.id)
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
