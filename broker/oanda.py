import json
import time

import requests

try:
    from oandapyV20 import API  # type: ignore
    from oandapyV20.endpoints.pricing import PricingStream  # type: ignore
    _HAS_OANDA_V20 = True
except Exception:
    API = None
    PricingStream = None
    _HAS_OANDA_V20 = False


class OandaBroker:
    def __init__(self, api_key, account_id, environment="practice"):
        self.api_key = api_key
        self.environment = str(environment).lower()
        self.api = API(access_token=api_key, environment=environment) if _HAS_OANDA_V20 else None
        self.account_id = account_id
        self.base_url = (
            "https://api-fxpractice.oanda.com"
            if self.environment != "live"
            else "https://api-fxtrade.oanda.com"
        )

    def stream_prices(self, instruments, on_tick):
        if _HAS_OANDA_V20 and self.api is not None:
            self._stream_with_oandapy(instruments, on_tick)
            return
        self._stream_with_requests(instruments, on_tick)

    def _stream_with_oandapy(self, instruments, on_tick):
        params = {
            "instruments": ",".join(instruments),
            "snapshot":    "True",
        }

        r = PricingStream(accountID=self.account_id, params=params)

        for response in self.api.request(r):
            msg_type = response.get("type", "")
            if msg_type == "PRICE":
                on_tick(response)
            elif msg_type == "HEARTBEAT":
                pass
            time.sleep(0.005)

    def _stream_with_requests(self, instruments, on_tick):
        url = f"{self.base_url}/v3/accounts/{self.account_id}/pricing/stream"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        params = {
            "instruments": ",".join(instruments),
            "snapshot": "True",
        }

        with requests.get(url, headers=headers, params=params, timeout=90, stream=True) as resp:
            resp.raise_for_status()
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                msg_type = message.get("type", "")
                if msg_type == "PRICE":
                    on_tick(message)
                time.sleep(0.005)
