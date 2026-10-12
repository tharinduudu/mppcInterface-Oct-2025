#!/usr/bin/env python3
"""Checks shared by the DAC writer and temperature-compensation controller."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DAC = load_module("detector_dac", ROOT / "dac.py")
BIAS = load_module("detector_bias", ROOT / "biasAdj.py")


class DacCalibrationTests(unittest.TestCase):
    def test_calibration_endpoints(self):
        self.assertAlmostEqual(DAC.predicted_vlow(0), 0.0005, places=7)
        self.assertAlmostEqual(DAC.predicted_vlow(1023), 2.9973, places=7)

    def test_code_size_is_about_three_millivolts(self):
        step = DAC.predicted_vlow(501) - DAC.predicted_vlow(500)
        self.assertAlmostEqual(step * 1000.0, 2.9294, places=3)

    def test_bias_controller_uses_same_conversion(self):
        for code in (0, 1, 256, 753, 1023):
            self.assertAlmostEqual(BIAS.code_to_vlow(code), DAC.predicted_vlow(code), places=10)

    def test_voltage_code_round_trip(self):
        for code in (0, 1, 256, 753, 1023):
            recovered = BIAS.vlow_to_code(BIAS.code_to_vlow(code))
            self.assertLessEqual(abs(recovered - code), 1)


class TemperatureCompensationTests(unittest.TestCase):
    def test_warmer_temperature_reduces_low_side_voltage(self):
        delta = BIAS.compensation_delta_v(21.0, 20.0)
        self.assertTrue(math.isclose(delta, -0.054, abs_tol=1e-12))

    def test_colder_temperature_increases_low_side_voltage(self):
        delta = BIAS.compensation_delta_v(19.0, 20.0)
        self.assertTrue(math.isclose(delta, 0.054, abs_tol=1e-12))


if __name__ == "__main__":
    unittest.main()
