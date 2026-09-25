"""Dependency-free WGS84/local-UTM helpers.

The planning exchange format always uses ``[longitude, latitude]``.  Any
distance, projection or interpolation in metres is performed in the local UTM
zone selected from the data centre; coordinates are converted back only when
serialising the result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable


EARTH_A = 6378137.0
EARTH_E2 = 0.0066943799901413165
UTM_K0 = 0.9996


def _zone(longitude: float) -> int:
    return max(1, min(60, int((longitude + 180.0) // 6.0) + 1))


@dataclass(frozen=True)
class LocalUTM:
    """A WGS84 UTM frame chosen for one local planning dataset."""

    zone: int
    north: bool

    @property
    def epsg(self) -> str:
        return f"EPSG:{32600 + self.zone if self.north else 32700 + self.zone}"

    @property
    def central_meridian(self) -> float:
        return math.radians((self.zone - 1) * 6 - 180 + 3)

    @classmethod
    def from_points(cls, points: Iterable[tuple[float, float]]) -> "LocalUTM":
        values = list(points)
        if not values:
            raise ValueError("At least one longitude/latitude point is required")
        longitude = sum(point[0] for point in values) / len(values)
        latitude = sum(point[1] for point in values) / len(values)
        if not (-180.0 <= longitude <= 180.0 and -80.0 <= latitude <= 84.0):
            raise ValueError(f"Coordinates are outside the supported WGS84/UTM range: {(longitude, latitude)}")
        return cls(_zone(longitude), latitude >= 0.0)

    def forward(self, point: tuple[float, float]) -> tuple[float, float]:
        lon, lat = point
        lon_r, lat_r = math.radians(lon), math.radians(lat)
        ep2 = EARTH_E2 / (1.0 - EARTH_E2)
        n = EARTH_A / math.sqrt(1.0 - EARTH_E2 * math.sin(lat_r) ** 2)
        t = math.tan(lat_r) ** 2
        c = ep2 * math.cos(lat_r) ** 2
        a = math.cos(lat_r) * (lon_r - self.central_meridian)
        m = EARTH_A * (
            (1 - EARTH_E2 / 4 - 3 * EARTH_E2**2 / 64 - 5 * EARTH_E2**3 / 256) * lat_r
            - (3 * EARTH_E2 / 8 + 3 * EARTH_E2**2 / 32 + 45 * EARTH_E2**3 / 1024) * math.sin(2 * lat_r)
            + (15 * EARTH_E2**2 / 256 + 45 * EARTH_E2**3 / 1024) * math.sin(4 * lat_r)
            - (35 * EARTH_E2**3 / 3072) * math.sin(6 * lat_r)
        )
        x = UTM_K0 * n * (
            a + (1 - t + c) * a**3 / 6
            + (5 - 18 * t + t**2 + 72 * c - 58 * ep2) * a**5 / 120
        ) + 500000.0
        y = UTM_K0 * (
            m + n * math.tan(lat_r) * (
                a**2 / 2 + (5 - t + 9 * c + 4 * c**2) * a**4 / 24
                + (61 - 58 * t + t**2 + 600 * c - 330 * ep2) * a**6 / 720
            )
        )
        if not self.north:
            y += 10000000.0
        return x, y

    def inverse(self, point: tuple[float, float]) -> tuple[float, float]:
        x, y = point
        if not self.north:
            y -= 10000000.0
        ep2 = EARTH_E2 / (1.0 - EARTH_E2)
        m = y / UTM_K0
        mu = m / (EARTH_A * (1 - EARTH_E2 / 4 - 3 * EARTH_E2**2 / 64 - 5 * EARTH_E2**3 / 256))
        e1 = (1 - math.sqrt(1 - EARTH_E2)) / (1 + math.sqrt(1 - EARTH_E2))
        phi1 = (
            mu + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
            + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
            + (151 * e1**3 / 96) * math.sin(6 * mu)
            + (1097 * e1**4 / 512) * math.sin(8 * mu)
        )
        n1 = EARTH_A / math.sqrt(1 - EARTH_E2 * math.sin(phi1) ** 2)
        t1 = math.tan(phi1) ** 2
        c1 = ep2 * math.cos(phi1) ** 2
        r1 = EARTH_A * (1 - EARTH_E2) / (1 - EARTH_E2 * math.sin(phi1) ** 2) ** 1.5
        d = (x - 500000.0) / (n1 * UTM_K0)
        lat = phi1 - (n1 * math.tan(phi1) / r1) * (
            d**2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * ep2) * d**4 / 24
            + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * ep2 - 3 * c1**2) * d**6 / 720
        )
        lon = self.central_meridian + (
            d - (1 + 2 * t1 + c1) * d**3 / 6
            + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * ep2 + 24 * t1**2) * d**5 / 120
        ) / math.cos(phi1)
        return math.degrees(lon), math.degrees(lat)

    def distance(self, first: tuple[float, float], second: tuple[float, float]) -> float:
        a, b = self.forward(first), self.forward(second)
        return math.hypot(a[0] - b[0], a[1] - b[1])

    def interpolate(self, first: tuple[float, float], second: tuple[float, float], ratio: float) -> tuple[float, float]:
        a, b = self.forward(first), self.forward(second)
        return self.inverse((a[0] + ratio * (b[0] - a[0]), a[1] + ratio * (b[1] - a[1])))

