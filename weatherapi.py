"""WeatherAPI.com adapter: normalizes one provider into the application's forecast schema."""
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

BASE_URL = "https://api.weatherapi.com/v1/forecast.json"


async def fetch_forecast(lat: float, lon: float, api_key: str) -> dict:
    params = {
        "key": api_key,
        "q": f"{lat},{lon}",
        "days": 3,
        "aqi": "no",
        "alerts": "no",
    }
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.get(BASE_URL, params=params)
        response.raise_for_status()
        data = response.json()

    location = data["location"]
    forecast_days = data["forecast"]["forecastday"]
    tz = ZoneInfo(location["tz_id"])
    now = datetime.now(tz).replace(tzinfo=None)
    hourly_rows = []
    for day in forecast_days:
        for item in day["hour"]:
            stamp = datetime.strptime(item["time"], "%Y-%m-%d %H:%M")
            if stamp >= now.replace(minute=0, second=0, microsecond=0):
                hourly_rows.append(item)
    if not hourly_rows:
        raise ValueError("WeatherAPI returned no upcoming hourly forecast")

    hourly = {
        "time": [],
        "temperature_2m_weatherapi": [],
        "precipitation_weatherapi": [],
        "wind_speed_10m_weatherapi": [],
        "wind_gusts_10m_weatherapi": [],
        "snowfall_weatherapi": [],
        "cloud_cover_weatherapi": [],
        "relative_humidity_2m_weatherapi": [],
        "apparent_temperature_weatherapi": [],
        "uv_index_weatherapi": [],
        "cape_weatherapi": [],
    }
    for item in hourly_rows:
        hourly["time"].append(item["time"].replace(" ", "T"))
        hourly["temperature_2m_weatherapi"].append(item.get("temp_c"))
        hourly["precipitation_weatherapi"].append(item.get("precip_mm"))
        hourly["wind_speed_10m_weatherapi"].append(item.get("wind_kph"))
        hourly["wind_gusts_10m_weatherapi"].append(item.get("gust_kph"))
        hourly["snowfall_weatherapi"].append(item.get("snow_cm"))
        hourly["cloud_cover_weatherapi"].append(item.get("cloud"))
        hourly["relative_humidity_2m_weatherapi"].append(item.get("humidity"))
        hourly["apparent_temperature_weatherapi"].append(item.get("feelslike_c"))
        hourly["uv_index_weatherapi"].append(item.get("uv"))
        # WeatherAPI does not provide CAPE in this response; leave unavailable.
        hourly["cape_weatherapi"].append(None)

    daily = {
        "time": [],
        "temperature_2m_max_weatherapi": [],
        "temperature_2m_min_weatherapi": [],
        "precipitation_sum_weatherapi": [],
    }
    for day in forecast_days:
        daily["time"].append(day["date"])
        daily["temperature_2m_max_weatherapi"].append(day["day"].get("maxtemp_c"))
        daily["temperature_2m_min_weatherapi"].append(day["day"].get("mintemp_c"))
        daily["precipitation_sum_weatherapi"].append(day["day"].get("totalprecip_mm"))

    offset_seconds = int(datetime.now(tz).utcoffset().total_seconds())
    return {
        "hourly": hourly,
        "daily": daily,
        "utc_offset_seconds": offset_seconds,
        "_models": ["weatherapi"],
        "_source": "WeatherAPI.com",
    }
