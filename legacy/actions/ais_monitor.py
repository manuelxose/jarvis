from __future__ import annotations

import logging
import math
import socket
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


LOGGER = logging.getLogger(__name__)

AIS_CHARSET = "@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_ !\"#$%&'()*+,-./0123456789:;<=>?"


def _sixbit_char(value: int) -> str:
    if 0 <= value < len(AIS_CHARSET):
        return AIS_CHARSET[value]
    return " "


def _payload_to_bits(payload: str) -> str:
    bits = []
    for char in payload:
        value = ord(char) - 48
        if value > 40:
            value -= 8
        bits.append(f"{value:06b}")
    return "".join(bits)


def _twos_complement(value: int, width: int) -> int:
    sign_bit = 1 << (width - 1)
    return value - (1 << width) if value & sign_bit else value


def _decode_ship_name(bits: str) -> str:
    chars = []
    for i in range(0, len(bits), 6):
        chunk = bits[i : i + 6]
        if len(chunk) < 6:
            break
        chars.append(_sixbit_char(int(chunk, 2)))
    return "".join(chars).replace("@", " ").strip()


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius_km * c


@dataclass
class AISDecoded:
    mmsi: int
    message_type: int
    lat: float | None = None
    lon: float | None = None
    sog_knots: float | None = None
    cog_degrees: float | None = None
    heading_degrees: int | None = None
    vessel_name: str | None = None
    vessel_type: int | None = None


class AISMonitor:
    """Read and parse AIS NMEA stream from localhost decoder (TCP 10110)."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 10110,
        timeout_seconds: float = 2.0,
        center_lat: float | None = None,
        center_lon: float | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout_seconds = timeout_seconds
        self.center_lat = center_lat
        self.center_lon = center_lon
        self._signal_timestamps: deque[datetime] = deque()

    def _connect(self) -> socket.socket:
        return socket.create_connection((self.host, self.port), timeout=self.timeout_seconds)

    def _collect_nmea_sentences(self, listen_seconds: float = 2.0, max_lines: int = 500) -> list[str]:
        lines: list[str] = []
        buffer = ""
        deadline = time.monotonic() + listen_seconds

        with self._connect() as sock:
            sock.settimeout(self.timeout_seconds)
            while time.monotonic() < deadline and len(lines) < max_lines:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue

                if not chunk:
                    break

                buffer += chunk.decode("ascii", errors="ignore")
                while "\n" in buffer and len(lines) < max_lines:
                    line, buffer = buffer.split("\n", 1)
                    clean = line.strip()
                    if clean.startswith("!AIVDM"):
                        lines.append(clean)
                        self._signal_timestamps.append(datetime.now(timezone.utc))

        self._prune_signal_history()
        return lines

    def _prune_signal_history(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        while self._signal_timestamps and self._signal_timestamps[0] < cutoff:
            self._signal_timestamps.popleft()

    def _decode_nmea(self, nmea_sentence: str) -> AISDecoded | None:
        try:
            parts = nmea_sentence.split(",")
            if len(parts) < 6:
                return None

            fragment_count = int(parts[1])
            fragment_number = int(parts[2])
            if fragment_count != 1 or fragment_number != 1:
                # Fragment assembly intentionally skipped for simplicity.
                return None

            payload = parts[5]
            bitstream = _payload_to_bits(payload)
            message_type = int(bitstream[0:6], 2)

            if message_type in {1, 2, 3}:
                mmsi = int(bitstream[8:38], 2)
                sog_raw = int(bitstream[50:60], 2)
                lon_raw = int(bitstream[61:89], 2)
                lat_raw = int(bitstream[89:116], 2)
                cog_raw = int(bitstream[116:128], 2)
                heading_raw = int(bitstream[128:137], 2)

                lon = _twos_complement(lon_raw, 28) / 600000.0
                lat = _twos_complement(lat_raw, 27) / 600000.0

                sog = None if sog_raw >= 1023 else sog_raw / 10.0
                cog = None if cog_raw >= 3600 else cog_raw / 10.0
                heading = None if heading_raw >= 511 else heading_raw

                return AISDecoded(
                    mmsi=mmsi,
                    message_type=message_type,
                    lat=lat,
                    lon=lon,
                    sog_knots=sog,
                    cog_degrees=cog,
                    heading_degrees=heading,
                )

            if message_type == 5:
                mmsi = int(bitstream[8:38], 2)
                vessel_name = _decode_ship_name(bitstream[112:232])
                vessel_type = int(bitstream[232:240], 2) if len(bitstream) >= 240 else None
                return AISDecoded(
                    mmsi=mmsi,
                    message_type=message_type,
                    vessel_name=vessel_name,
                    vessel_type=vessel_type,
                )

            return None
        except Exception:
            return None

    def get_nearby_vessels(self, radius_km: float = 50.0) -> list[dict[str, Any]]:
        sentences = self._collect_nmea_sentences()

        dynamic_by_mmsi: dict[int, AISDecoded] = {}
        static_by_mmsi: dict[int, AISDecoded] = {}

        for sentence in sentences:
            decoded = self._decode_nmea(sentence)
            if not decoded:
                continue

            if decoded.message_type in {1, 2, 3}:
                dynamic_by_mmsi[decoded.mmsi] = decoded
            elif decoded.message_type == 5:
                static_by_mmsi[decoded.mmsi] = decoded

        vessels: list[dict[str, Any]] = []
        for mmsi, dynamic_data in dynamic_by_mmsi.items():
            static_data = static_by_mmsi.get(mmsi)
            vessel: dict[str, Any] = {
                "mmsi": mmsi,
                "name": static_data.vessel_name if static_data else None,
                "type": static_data.vessel_type if static_data else None,
                "lat": dynamic_data.lat,
                "lon": dynamic_data.lon,
                "speed_knots": dynamic_data.sog_knots,
                "course_degrees": dynamic_data.cog_degrees,
                "heading_degrees": dynamic_data.heading_degrees,
            }

            if (
                self.center_lat is not None
                and self.center_lon is not None
                and dynamic_data.lat is not None
                and dynamic_data.lon is not None
            ):
                distance = _haversine_km(
                    self.center_lat,
                    self.center_lon,
                    dynamic_data.lat,
                    dynamic_data.lon,
                )
                vessel["distance_km"] = round(distance, 2)
                if distance > radius_km:
                    continue

            vessels.append(vessel)

        vessels.sort(key=lambda item: item.get("distance_km", 999999))
        return vessels

    def get_ais_status(self) -> dict[str, Any]:
        active = False
        error = None
        try:
            with self._connect():
                active = True
        except Exception as exc:
            error = str(exc)

        self._prune_signal_history()
        last_signal = self._signal_timestamps[-1].isoformat() if self._signal_timestamps else None
        return {
            "activo": active,
            "error": error,
            "senales_recibidas_ultima_hora": len(self._signal_timestamps),
            "ultima_senal": last_signal,
        }

    def format_vessel_report(self, vessels: list[dict[str, Any]]) -> str:
        if not vessels:
            return "No he detectado barcos cercanos en este momento."

        count = len(vessels)
        nearest = vessels[0]
        ship_name = nearest.get("name") or f"MMSI {nearest.get('mmsi')}"
        speed = nearest.get("speed_knots")
        speed_text = f"{speed:.1f} nudos" if isinstance(speed, (float, int)) else "velocidad desconocida"

        distance = nearest.get("distance_km")
        if isinstance(distance, (float, int)):
            nearest_text = f"El mas cercano es {ship_name} a {distance:.1f} kilometros, navegando a {speed_text}."
        else:
            nearest_text = f"Uno destacado es {ship_name}, navegando a {speed_text}."

        return f"Hay {count} barcos detectados. {nearest_text}"

