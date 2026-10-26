"""Earth Engine-backed temporal land-cover evidence for a pilot area."""

from datetime import date, timedelta
from statistics import fmean


CLASS_BANDS = (
    "water",
    "trees",
    "grass",
    "flooded_vegetation",
    "crops",
    "shrub_and_scrub",
    "built",
    "bare",
    "snow_and_ice",
)

CLASS_LABELS = {
    "water": "water",
    "trees": "trees",
    "grass": "grass",
    "flooded_vegetation": "flooded vegetation",
    "crops": "crops",
    "shrub_and_scrub": "shrub and scrub",
    "built": "built-up",
    "bare": "bare or sparsely vegetated",
    "snow_and_ice": "snow and ice",
}


def _month_ranges(start_date: str, end_date: str) -> list[dict[str, str]]:
    """Return inclusive monthly bins covering the requested dates."""
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if start >= end:
        raise ValueError("The start date must be earlier than the end date.")

    ranges = []
    cursor = start.replace(day=1)
    final_day = end + timedelta(days=1)
    while cursor < final_day:
        next_month = (
            date(cursor.year + 1, 1, 1)
            if cursor.month == 12
            else date(cursor.year, cursor.month + 1, 1)
        )
        bin_start = max(cursor, start)
        bin_end = min(next_month, final_day)
        ranges.append({
            "start": bin_start.isoformat(),
            "end": bin_end.isoformat(),
            "month": cursor.strftime("%Y-%m"),
        })
        cursor = next_month
    return ranges


def fetch_dynamic_world_series(
    latitude: float,
    longitude: float,
    start_date: str,
    end_date: str,
    area_size: float = 0.01,
    project: str | None = None,
) -> list[dict]:
    """Fetch monthly clear-pixel land-cover probabilities for the region."""
    try:
        import ee
    except ImportError as error:
        raise RuntimeError(
            "Temporal land-cover analysis requires earthengine-api. "
            "Install requirements-temporal.txt."
        ) from error

    if not project:
        raise RuntimeError(
            "Set EARTH_ENGINE_PROJECT and authenticate Earth Engine before "
            "running temporal analysis."
        )

    try:
        ee.Initialize(project=project)
    except ee.EEException as error:
        raise RuntimeError(
            f"Earth Engine initialization failed: {error}"
        ) from error
    region = ee.Geometry.Rectangle(
        [
            longitude - area_size,
            latitude - area_size,
            longitude + area_size,
            latitude + area_size,
        ],
        geodesic=False,
    )
    months = ee.List(_month_ranges(start_date, end_date))

    def summarize_month(month):
        month = ee.Dictionary(month)
        start = ee.Date(month.get("start"))
        end = ee.Date(month.get("end"))
        collection = (
            ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
            .filterBounds(region)
            .filterDate(start, end)
        )
        composite = collection.select(list(CLASS_BANDS)).median()
        probabilities = composite.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=region,
            scale=10,
            maxPixels=100000000,
            tileScale=4,
        )
        clear_mask = (
            collection.select("label")
            .count()
            .gt(0)
            .unmask(0)
            .clip(region)
            .rename("clear")
        )
        clear_stats = clear_mask.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=region,
            scale=10,
            maxPixels=100000000,
            tileScale=4,
        )
        properties = ee.Dictionary({
            "month": month.get("month"),
            "scene_count": collection.size(),
            "clear_fraction": clear_stats.get("clear"),
        }).combine(probabilities, overwrite=True)
        return ee.Feature(None, properties)

    try:
        result = ee.FeatureCollection(months.map(summarize_month)).getInfo()
    except ee.EEException as error:
        raise RuntimeError(
            f"Earth Engine could not fetch the monthly series: {error}"
        ) from error
    return [
        feature.get("properties", {})
        for feature in result.get("features", [])
    ]


def assess_temporal_patterns(
    monthly_series: list[dict],
    minimum_clear_percent: float = 80,
    minimum_months_per_period: int = 8,
) -> dict:
    """Compare two seasonal windows and report interpretable pattern signals."""
    usable = []
    for row in monthly_series:
        clear_fraction = row.get("clear_fraction")
        scene_count = row.get("scene_count", 0)
        if clear_fraction is None or not scene_count:
            continue
        clear_percent = float(clear_fraction) * 100
        if clear_percent >= minimum_clear_percent:
            usable.append(row)

    result = {
        "model": "Google Dynamic World V1 (pre-trained land-cover model)",
        "method": "Monthly median class probabilities compared across time.",
        "status": "insufficient_temporal_evidence",
        "usable_months": len(usable),
        "patterns": [],
        "class_changes_percentage_points": {},
        "legality": "unverified",
        "caveat": (
            "This reports regional land-cover pattern signals, not verified "
            "causes or legal findings. Bare-ground change alone does not prove "
            "mining; vegetation loss alone does not prove deforestation."
        ),
    }
    if len(usable) < minimum_months_per_period * 2:
        result["required_usable_months"] = minimum_months_per_period * 2
        return result

    usable_by_month = {row["month"]: row for row in usable}
    all_months = sorted(
        row["month"] for row in monthly_series if row.get("month")
    )
    if not all_months:
        result["required_usable_months"] = minimum_months_per_period * 2
        return result

    last_month = date.fromisoformat(all_months[-1] + "-01")

    def subtract_months(value: date, count: int) -> date:
        month_index = value.year * 12 + value.month - 1 - count
        return date(month_index // 12, month_index % 12 + 1, 1)

    baseline_start = subtract_months(last_month, 23)
    current_start = subtract_months(last_month, 11)

    def period_months(start: date, count: int) -> list[str]:
        months = []
        cursor = start
        for _ in range(count):
            months.append(cursor.strftime("%Y-%m"))
            cursor = subtract_months(cursor, -1)
        return months

    baseline_period = period_months(baseline_start, 12)
    recent_period = period_months(current_start, 12)
    baseline = [
        usable_by_month[month]
        for month in baseline_period if month in usable_by_month
    ]
    recent = [
        usable_by_month[month]
        for month in recent_period if month in usable_by_month
    ]
    if (
        len(baseline) < minimum_months_per_period
        or len(recent) < minimum_months_per_period
    ):
        result["required_months_per_year_window"] = minimum_months_per_period
        return result

    def mean_probability(rows: list[dict], band: str) -> float | None:
        values = [float(row[band]) for row in rows if row.get(band) is not None]
        return fmean(values) if values else None

    changes = {}
    for band in CLASS_BANDS:
        before = mean_probability(baseline, band)
        after = mean_probability(recent, band)
        if before is not None and after is not None:
            changes[band] = round((after - before) * 100, 2)

    result.update({
        "status": "assessed",
        "baseline_months": len(baseline),
        "recent_months": len(recent),
        "baseline_period": [baseline_period[0], baseline_period[-1]],
        "recent_period": [recent_period[0], recent_period[-1]],
        "class_changes_percentage_points": changes,
    })

    patterns = []
    if changes.get("built", 0) >= 10:
        patterns.append({
            "type": "possible_built_up_expansion",
            "label": "Possible built-up expansion",
            "change_percentage_points": changes["built"],
        })
    if changes.get("trees", 0) <= -12:
        patterns.append({
            "type": "possible_tree_cover_decline",
            "label": "Possible tree-cover decline; verify before calling it deforestation",
            "change_percentage_points": changes["trees"],
        })
    if changes.get("water", 0) <= -12:
        patterns.append({
            "type": "possible_water_area_decline",
            "label": "Possible water-area decline",
            "change_percentage_points": changes["water"],
        })
    if changes.get("water", 0) >= 12:
        patterns.append({
            "type": "possible_water_area_increase",
            "label": "Possible water-area increase",
            "change_percentage_points": changes["water"],
        })
    if changes.get("bare", 0) >= 12:
        patterns.append({
            "type": "bare_ground_increase",
            "label": "Bare or sparse-ground increase; cause undetermined",
            "change_percentage_points": changes["bare"],
        })

    result["patterns"] = patterns or [{
        "type": "no_strong_regional_pattern",
        "label": "No strong regional land-cover trend identified",
    }]
    return result
