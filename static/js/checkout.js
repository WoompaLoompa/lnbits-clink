const sats = msat => Math.round(msat / 1000).toLocaleString() + ' sats'

window.app = Vue.createApp({
  el: '#vue',
  mixins: [windowMixin],
  components: {
    QrcodeVue: QrcodeVue.default
  },
  data() {
    return {
      amount: null,
      loading: false,
      bolt11: ''
    }
  },
  methods: {
    sats,
    copy(text) {
      LNbits.utils.copyText(text)
      this.$q.notify({ type: 'positive', message: 'Copied' })
    },
    async createInvoice() {
      this.loading = true
      this.bolt11 = ''
      try {
        const resp = await axios.post('/clink/api/v1/checkout/{{ offer_id }}', {
          amount_sats: this.amount
        })
        this.bolt11 = resp.data.bolt11
      } catch (e) {
        LNbits.utils.notifyApiError(e)
      } finally {
        this.loading = false
      }
    }
  }
})
