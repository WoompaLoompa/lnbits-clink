const sats = n => Number(n).toLocaleString() + ' sats'

window.app = Vue.createApp({
  el: '#vue',
  mixins: [windowMixin],
  data() {
    return {
      wallets: this.g.user.wallets,
      wallet: this.g.user.wallets[0],
      noffer: '',
      amount: null,
      loading: false,
      parsing: false,
      info: null,
      result: null,
      error: null
    }
  },
  methods: {
    sats,
    copy(text) {
      LNbits.utils.copyText(text)
      this.$q.notify({ type: 'positive', message: 'Copied' })
    },
    async parseNoffer() {
      if (!this.noffer) return
      this.parsing = true
      this.info = null
      this.error = null
      try {
        const resp = await axios.post('/clink/api/v1/pay/parse', { noffer: this.noffer })
        this.info = resp.data
        if (resp.data.price_type === 0) {
          this.amount = resp.data.price
        }
      } catch (e) {
        LNbits.utils.notifyApiError(e)
      } finally {
        this.parsing = false
      }
    },
    async pay() {
      if (!this.wallet || !this.noffer) return
      this.loading = true
      this.error = null
      this.result = null
      try {
        const body = {
          wallet: this.wallet.id,
          noffer: this.noffer,
          amount_sats: this.amount || null,
          description: 'CLINK offer payment'
        }
        const resp = await LNbits.api.request(
          'POST',
          '/clink/api/v1/pay',
          this.wallet.adminkey,
          body
        )
        this.result = resp.data
        this.$q.notify({ type: 'positive', message: 'Payment sent' })
      } catch (e) {
        this.error = e.response && e.response.data && e.response.data.detail
          ? e.response.data.detail
          : String(e.message || e)
        if (typeof this.error === 'object') {
          this.error = this.error.error || JSON.stringify(this.error)
        }
        LNbits.utils.notifyApiError(e)
      } finally {
        this.loading = false
      }
    }
  }
})
