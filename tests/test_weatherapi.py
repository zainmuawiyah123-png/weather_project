import asyncio
import weatherapi


class FakeResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {
            "location": {"tz_id": "Asia/Amman"},
            "forecast": {
                "forecastday": [{
                    "date": "2026-11-07",
                    "day": {"maxtemp_c": 19.0, "mintemp_c": 11.0, "totalprecip_mm": 2.4},
                    "hour": [{
                        "time": "2026-11-07 23:00",
                        "temp_c": 14.0, "precip_mm": 0.2, "wind_kph": 10,
                        "gust_kph": 15, "snow_cm": 0, "cloud": 50,
                        "humidity": 60, "feelslike_c": 13.0, "uv": 0,
                    }, {
                        "time": "2026-11-08 00:00",
                        "temp_c": 13.0, "precip_mm": 0.0, "wind_kph": 8,
                        "gust_kph": 12, "snow_cm": 0, "cloud": 40,
                        "humidity": 65, "feelslike_c": 12.0, "uv": 0,
                    }],
                }]
            },
        }


class FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, url, params):
        assert url == weatherapi.BASE_URL
        assert params["key"] == "test-key"
        assert params["q"] == "31.95,35.91"
        return FakeResponse()


def test_weatherapi_adapter_normalizes_forecast(monkeypatch):
    monkeypatch.setattr(weatherapi.httpx, "AsyncClient", FakeClient)
    result = asyncio.run(weatherapi.fetch_forecast(31.95, 35.91, "test-key"))

    assert result["_models"] == ["weatherapi"]
    assert result["_source"] == "WeatherAPI.com"
    assert result["hourly"]["temperature_2m_weatherapi"]
    assert result["daily"]["temperature_2m_max_weatherapi"] == [19.0]
    assert result["hourly"]["cape_weatherapi"] == [None, None]
