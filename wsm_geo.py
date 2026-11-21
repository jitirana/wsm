"""wsm_geo.py - Maidenhead locators: centre of a square, distance and bearing from the station, Google Maps link."""
import math


def grid_to_latlon(grid):
    """4 or 6 character Maidenhead locator -> (lat, lon) of the CENTRE of the square (degrees), or None if it is not valid."""
    g = (grid or "").strip()
    if len(g) not in (4, 6, 8):
        return None
    try:
        a, b = g[0].upper(), g[1].upper()
        if not ("A" <= a <= "R" and "A" <= b <= "R"):
            return None
        lon = (ord(a) - 65) * 20.0 - 180.0
        lat = (ord(b) - 65) * 10.0 - 90.0
        lon += int(g[2]) * 2.0
        lat += int(g[3]) * 1.0
        w, h = 2.0, 1.0
        if len(g) >= 6:
            c, d = g[4].upper(), g[5].upper()
            if not ("A" <= c <= "X" and "A" <= d <= "X"):
                return None
            lon += (ord(c) - 65) * (2.0 / 24.0)
            lat += (ord(d) - 65) * (1.0 / 24.0)
            w, h = 2.0 / 24.0, 1.0 / 24.0
        if len(g) == 8:
            lon += int(g[6]) * (2.0 / 240.0)
            lat += int(g[7]) * (1.0 / 240.0)
            w, h = 2.0 / 240.0, 1.0 / 240.0
        return lat + h / 2.0, lon + w / 2.0
    except (ValueError, IndexError):
        return None


def distance_bearing(lat1, lon1, lat2, lon2):
    """great-circle distance (km) and initial bearing (degrees from north) from point 1 to point 2"""
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    dp = p2 - p1
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    d = 2 * R * math.asin(min(1.0, math.sqrt(h)))
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return d, (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def maps_url(lat, lon):
    """Google Maps link that drops a pin at the centre of the locator square"""
    return f"https://www.google.com/maps?q={lat:.4f},{lon:.4f}"
