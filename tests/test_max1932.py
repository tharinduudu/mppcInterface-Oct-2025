"""Exercise the MAX1932 CLI without a Pi or access to GPIO and SPI."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SOURCE = (Path(__file__).resolve().parents[1]
          / "mppcInterface/firmware/libraries/max1932")


class Max1932CLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        folder = Path(temp.name)
        cls.binary = folder / "max1932"
        cls.bad_config = folder / "invalid.conf"
        cls.bad_config.write_text("not a calibration\n")
        # Any accidental hardware call fails the test. One test opts into a
        # mock SPI short write to check that the command does not report success.
        (folder / "wiringPi.h").write_text("""
            #pragma once
            #include <cstdlib>
            #define OUTPUT 1
            #define HIGH 1
            #define LOW 0
            inline int wiringPiSetup() {
              if (!std::getenv("TEST_SHORT_WRITE")) std::exit(91);
              return 0;
            }
            inline void pinMode(int, int) {}
            inline void digitalWrite(int, int) {}
            inline void delay(unsigned) {}
        """)
        (folder / "wiringPiSPI.h").write_text("""
            #pragma once
            inline int wiringPiSPISetupMode(int, int, int) { return 0; }
            inline int wiringPiSPIDataRW(int, unsigned char*, int) { return 0; }
        """)
        subprocess.run([os.environ.get("CXX", "c++"), "-std=c++11", "-I", str(folder),
                        str(SOURCE / "main.cpp"), str(SOURCE / "max1932.cpp"),
                        "-o", str(cls.binary)], check=True)

    def command(self, *args, **kwargs):
        return subprocess.run([str(self.binary), *map(str, args)], capture_output=True,
                              text=True, **kwargs)

    def test_raw_prediction(self):
        result = self.command("--predict", "--calibration",
                              SOURCE / "calibration.example.conf", "F8", check=True)
        self.assertIn("0xF8 (248)", result.stdout)
        self.assertIn("55.134 V (calibrated estimate, not readback)", result.stdout)

    def test_voltage_prediction(self):
        for args in (("--voltage", "55.134"), ("55.134V",), ("--mV", "55134")):
            result = self.command("--predict", "--calibration",
                                  SOURCE / "calibration.example.conf", *args, check=True)
            self.assertIn("0xF8 (248)", result.stdout)

    def test_invalid_requests_do_not_touch_hardware(self):
        for args in ((), ("0x100",), ("0xEAg",), ("--voltage", "nan"),
                     ("--calibration", SOURCE / "calibration.example.conf", "--voltage", "50"),
                     ("--calibration", self.bad_config, "EA")):
            result = self.command(*args)
            self.assertEqual(result.returncode, 1, result.stderr)

    def test_code_outside_curve_is_unknown(self):
        result = self.command("--predict", "--calibration", SOURCE / "calibration.example.conf",
                              "0x44", check=True)
        self.assertIn("Voltage unknown", result.stdout)

    def test_off_prediction_survives_bad_config(self):
        result = self.command("--predict", "--calibration", self.bad_config, "0", check=True)
        self.assertIn("0 V (off command)", result.stdout)

    def test_spi_failure_exits_nonzero(self):
        result = self.command("0", env={**os.environ, "TEST_SHORT_WRITE": "1"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("SPI write failed", result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
