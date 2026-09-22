"""Open-Meteo responses normalized through caller-supplied transport."""

import json


WMO = {0:"clear",1:"mainly clear",2:"partly cloudy",3:"overcast",45:"fog",48:"rime fog",
       51:"light drizzle",53:"drizzle",55:"dense drizzle",56:"freezing drizzle",57:"freezing drizzle",
       61:"light rain",63:"rain",65:"heavy rain",66:"freezing rain",67:"freezing rain",
       71:"light snow",73:"snow",75:"heavy snow",77:"snow grains",80:"rain showers",81:"rain showers",
       82:"violent showers",85:"snow showers",86:"snow showers",95:"thunderstorm",96:"thunderstorm+hail",99:"thunderstorm+hail"}


WEATHER_PROVIDER_FIELDS = {
    "weather_code", "temp_max_c", "temp_min_c", "feels_like_max_c",
    "feels_like_min_c", "precipitation_mm", "rain_mm", "snowfall_cm",
    "precipitation_hours", "wind_speed_max_ms", "wind_gusts_max_ms",
    "wind_dir_deg", "sunrise", "sunset", "daylight_hours", "sunshine_hours",
    "uv_index_max", "uv_index_clear_sky_max", "solar_radiation_mj", "et0_mm",
    "humidity_mean_pct", "pressure_mean_hpa", "cloud_cover_mean_pct",
}


AIR_PROVIDER_FIELDS = {
    "european_aqi_mean", "european_aqi_max", "pm2_5_ugm3", "pm10_ugm3",
    "ozone_ugm3", "no2_ugm3", "so2_ugm3", "co_ugm3", "alder_pollen",
    "birch_pollen", "grass_pollen", "mugwort_pollen", "olive_pollen",
    "ragweed_pollen",
}


def missing_provider_fields(row, expected):
    """List missing expected values while retaining valid zero measurements."""
    return sorted(field for field in expected if row.get(field) is None)


def read_weather(a, *, day, open_url):
    """Fetch metric weather and return its canonical row, confirmation and omissions."""
    d = day
    daily = ("weather_code,temperature_2m_max,temperature_2m_min,apparent_temperature_max,apparent_temperature_min,"
             "sunrise,sunset,daylight_duration,sunshine_duration,uv_index_max,uv_index_clear_sky_max,"
             "precipitation_sum,rain_sum,snowfall_sum,precipitation_hours,wind_speed_10m_max,wind_gusts_10m_max,"
             "wind_direction_10m_dominant,shortwave_radiation_sum,et0_fao_evapotranspiration")
    hourly = "relative_humidity_2m,surface_pressure,cloud_cover"
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&daily=%s&hourly=%s"
           "&timezone=auto&temperature_unit=celsius&wind_speed_unit=ms&precipitation_unit=mm"
           "&start_date=%s&end_date=%s" % (a.lat, a.lon, daily, hourly, d, d))
    with open_url(url, timeout=25) as r:
        j = json.load(r)
    dl = j["daily"]; hh = j.get("hourly", {})
    def g(k):
        v = dl.get(k); return v[0] if v else None
    def hrs(k):
        v = g(k); return round(v/3600, 2) if v is not None else None
    def mean(k):
        vals = [x for x in hh.get(k, []) if x is not None]
        return round(sum(vals)/len(vals), 1) if vals else None
    cond = WMO.get(g("weather_code"), str(g("weather_code")))
    row = {
        "date": d, "location": a.location, "weather_code": g("weather_code"), "condition": cond,
        "temp_max_c": g("temperature_2m_max"), "temp_min_c": g("temperature_2m_min"),
        "feels_like_max_c": g("apparent_temperature_max"), "feels_like_min_c": g("apparent_temperature_min"),
        "precipitation_mm": g("precipitation_sum"), "rain_mm": g("rain_sum"), "snowfall_cm": g("snowfall_sum"),
        "precipitation_hours": g("precipitation_hours"),
        "wind_speed_max_ms": g("wind_speed_10m_max"), "wind_gusts_max_ms": g("wind_gusts_10m_max"),
        "wind_dir_deg": g("wind_direction_10m_dominant"),
        "sunrise": g("sunrise"), "sunset": g("sunset"),
        "daylight_hours": hrs("daylight_duration"), "sunshine_hours": hrs("sunshine_duration"),
        "uv_index_max": g("uv_index_max"), "uv_index_clear_sky_max": g("uv_index_clear_sky_max"),
        "solar_radiation_mj": g("shortwave_radiation_sum"), "et0_mm": g("et0_fao_evapotranspiration"),
        "humidity_mean_pct": mean("relative_humidity_2m"), "pressure_mean_hpa": mean("surface_pressure"),
        "cloud_cover_mean_pct": mean("cloud_cover"), "source": "open-meteo",
    }
    result = {"ok": True, "date": d, "location": a.location, "condition": cond,
         "temp_max_c": row["temp_max_c"], "feels_like_max_c": row["feels_like_max_c"],
         "sunshine_hours": row["sunshine_hours"], "daylight_hours": row["daylight_hours"],
         "uv_index_max": row["uv_index_max"], "wind_speed_max_ms": row["wind_speed_max_ms"],
         "collected_fields": len(row)}
    return row, result, missing_provider_fields(row, WEATHER_PROVIDER_FIELDS)


def read_air(a, *, day, open_url):
    """Air quality + pollen (Open-Meteo Air Quality API, hourly -> daily mean/max). All metric (ug/m3, grains/m3)."""
    d = day
    hourly = ("pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,sulphur_dioxide,ozone,european_aqi,"
              "alder_pollen,birch_pollen,grass_pollen,mugwort_pollen,olive_pollen,ragweed_pollen")
    url = ("https://air-quality-api.open-meteo.com/v1/air-quality?latitude=%s&longitude=%s&hourly=%s"
           "&timezone=auto&start_date=%s&end_date=%s" % (a.lat, a.lon, hourly, d, d))
    with open_url(url, timeout=25) as r:
        j = json.load(r)
    hh = j.get("hourly", {})
    def mean(k):
        v = [x for x in hh.get(k, []) if x is not None]; return round(sum(v)/len(v), 1) if v else None
    def mx(k):
        v = [x for x in hh.get(k, []) if x is not None]; return round(max(v), 1) if v else None
    row = {
        "date": d, "location": a.location,
        "european_aqi_mean": mean("european_aqi"), "european_aqi_max": mx("european_aqi"),
        "pm2_5_ugm3": mean("pm2_5"), "pm10_ugm3": mean("pm10"), "ozone_ugm3": mean("ozone"),
        "no2_ugm3": mean("nitrogen_dioxide"), "so2_ugm3": mean("sulphur_dioxide"), "co_ugm3": mean("carbon_monoxide"),
        "alder_pollen": mean("alder_pollen"), "birch_pollen": mean("birch_pollen"), "grass_pollen": mean("grass_pollen"),
        "mugwort_pollen": mean("mugwort_pollen"), "olive_pollen": mean("olive_pollen"), "ragweed_pollen": mean("ragweed_pollen"),
        "source": "open-meteo-aqi",
    }
    result = {"ok": True, "date": d, "european_aqi_mean": row["european_aqi_mean"], "european_aqi_max": row["european_aqi_max"],
         "pm2_5_ugm3": row["pm2_5_ugm3"], "grass_pollen": row["grass_pollen"], "birch_pollen": row["birch_pollen"],
         "collected_fields": len(row)}
    return row, result, missing_provider_fields(row, AIR_PROVIDER_FIELDS)
